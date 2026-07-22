"""Shared test construction without hidden global dependencies."""

import uuid
from collections.abc import Iterator
from functools import lru_cache

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import AppEnvironment, Settings

TEST_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\ntest-only\n-----END PRIVATE KEY-----"


@lru_cache(maxsize=1)
def production_test_private_key() -> str:
    """Create non-placeholder test key material without committing a credential fixture."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


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
        "frontend_url": None,
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
        "llm_provider": "deterministic",
        "llm_api_key": "llm-api-key-for-tests-only-00000000000",
    }
    requested_environment = overrides.get("app_env")
    if requested_environment in {AppEnvironment.DEVELOPMENT, "development"}:
        values["llm_provider"] = "openai"
    if requested_environment in {AppEnvironment.PRODUCTION, "production"}:
        values["database_url"] = (
            "postgresql+asyncpg://service:Pr0ductionFixtureCredential@"
            "db.example.com/repolume?ssl=require"
        )
        values["redis_url"] = (
            "rediss://service:RedisProductionFixtureCredential@redis.example.com/0"
        )
        values["embedding_service_url"] = "https://embeddings.example.com"
        values["embedding_service_token"] = "Emb3ddingProductionFixtureCredential-001"
        values["qdrant_url"] = "https://qdrant.example.com"
        values["qdrant_api_key"] = "Qdr4ntProductionFixtureCredential-00001"
        values["llm_provider"] = "openai"
        values["llm_api_url"] = "https://api.openai.com/v1"
        values["llm_api_key"] = "LlmProductionFixtureCredential-0000001"
        values["frontend_url"] = "https://app.repolume.example"
        values["github_client_id"] = "Iv1.production-fixture-client"
        values["github_client_secret"] = "G1thubProductionFixtureCredential-0001"
        values["github_app_private_key"] = production_test_private_key()
        values["github_webhook_secret"] = "Webh00kProductionFixtureCredential-001"
        values["access_token_secret"] = "AccessProductionFixtureCredential-00001"
        values["token_hash_secret"] = "HashProductionFixtureCredential-0000001"
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
