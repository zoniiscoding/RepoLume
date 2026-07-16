"""Shared test construction without hidden global dependencies."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.core.config import AppEnvironment, Settings


class FakeDatabase:
    """Explicit in-process readiness dependency for HTTP unit tests."""

    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.disposed = False

    async def is_ready(self) -> bool:
        return self.ready

    async def dispose(self) -> None:
        self.disposed = True


def make_settings(**overrides: object) -> Settings:
    """Build settings through normal Pydantic validation without reading environment."""
    values: dict[str, object] = {
        "app_env": AppEnvironment.TEST,
        "database_url": "postgresql+asyncpg://test:test@127.0.0.1:5432/repolume_test",
        "log_level": "INFO",
        "log_json": True,
        "docs_enabled": False,
        "cors_origins": [],
        "trusted_hosts": ["testserver", "localhost", "127.0.0.1"],
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def client(settings: Settings, fake_database: FakeDatabase) -> Iterator[TestClient]:
    app = create_app(settings=settings, database=fake_database)
    with TestClient(app) as test_client:
        yield test_client
