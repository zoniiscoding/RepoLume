"""Shared test construction without hidden global dependencies."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import AppEnvironment, Settings

TEST_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\ntest-only\n-----END PRIVATE KEY-----"


class FakeDatabase:
    """Explicit in-process readiness dependency for HTTP unit tests."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.disposed = False

    async def is_ready(self) -> bool:
        return self.ready

    async def dispose(self) -> None:
        self.disposed = True


class FakeJobQueue:
    """In-process queue dependency for HTTP unit tests."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.enqueued: list[uuid.UUID] = []
        self.closed = False

    async def is_ready(self) -> bool:
        return self.ready

    async def enqueue(self, job_id: uuid.UUID) -> None:
        self.enqueued.append(job_id)

    async def close(self) -> None:
        self.closed = True


class FakeVectorReadiness:
    """In-process Qdrant readiness boundary for HTTP unit tests."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.closed = False

    async def is_ready(self) -> bool:
        return self.ready

    async def close(self) -> None:
        self.closed = True


def make_settings(**overrides: object) -> Settings:
    """Build settings through normal Pydantic validation without reading environment."""
    values: dict[str, object] = {
        "app_env": AppEnvironment.TEST,
        "database_url": "postgresql+asyncpg://test:test@127.0.0.1:5432/repolume_test",
        "redis_url": "redis://127.0.0.1:6379/15",
        "log_level": "INFO",
        "log_json": True,
        "docs_enabled": False,
        "cors_origins": ["http://testserver"],
        "trusted_hosts": ["testserver", "localhost", "127.0.0.1"],
        "github_app_id": 12345,
        "github_client_id": "test-client-id",
        "github_client_secret": "github-client-secret-for-tests-only-000000",
        "github_app_private_key": TEST_PRIVATE_KEY,
        "github_webhook_secret": "github-webhook-secret-for-tests-only-0000",
        "github_oauth_callback_url": "http://testserver/api/v1/auth/github/callback",
        "access_token_secret": "access-token-secret-for-tests-only-0000000",
        "token_hash_secret": "token-hash-secret-for-tests-only-000000000",
        "embedding_service_token": "embedding-service-secret-for-tests-000000",
    }
    requested_environment = overrides.get("app_env")
    if requested_environment in {AppEnvironment.PRODUCTION, "production"}:
        values["redis_url"] = "rediss://service:secret@redis.example.com/0"
        values["embedding_service_url"] = "https://embeddings.example.com"
        values["qdrant_url"] = "https://qdrant.example.com"
        values["qdrant_api_key"] = "qdrant-api-key-for-tests-only-000000000"
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def fake_job_queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def fake_vector_store() -> FakeVectorReadiness:
    return FakeVectorReadiness()


@pytest.fixture
def client(
    settings: Settings,
    fake_database: FakeDatabase,
    fake_job_queue: FakeJobQueue,
    fake_vector_store: FakeVectorReadiness,
) -> Iterator[TestClient]:
    app = create_app(
        settings=settings,
        database=fake_database,
        job_queue=fake_job_queue,
        vector_store=fake_vector_store,
    )
    with TestClient(app) as test_client:
        yield test_client
