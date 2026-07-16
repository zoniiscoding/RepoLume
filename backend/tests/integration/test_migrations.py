"""Alembic verification against a disposable PostgreSQL database."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.conftest import TEST_PRIVATE_KEY
from tests.unit.test_models import EXPECTED_TABLES

pytestmark = pytest.mark.integration


def _test_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.fail("TEST_DATABASE_URL must target a disposable PostgreSQL database")
    return value


def _alembic_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    url = _test_database_url()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("REDIS_URL", os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15"))
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-client-secret-for-tests-only-000000")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", TEST_PRIVATE_KEY)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-webhook-secret-for-tests-only-0000")
    monkeypatch.setenv(
        "GITHUB_OAUTH_CALLBACK_URL",
        "http://testserver/api/v1/auth/github/callback",
    )
    monkeypatch.setenv("ACCESS_TOKEN_SECRET", "access-token-secret-for-tests-only-0000000")
    monkeypatch.setenv("TOKEN_HASH_SECRET", "token-hash-secret-for-tests-only-000000000")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "embedding-service-secret-for-tests-000000")
    root = Path(__file__).resolve().parents[2]
    return Config(str(root / "alembic.ini"))


async def _table_names() -> set[str]:
    engine = create_async_engine(_test_database_url())
    async with engine.connect() as connection:
        result = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    await engine.dispose()
    return result


def test_empty_database_upgrade_and_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _alembic_config(monkeypatch)
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    table_names = asyncio.run(_table_names())

    assert table_names == EXPECTED_TABLES

    command.downgrade(config, "base")
    remaining = asyncio.run(_table_names())
    assert remaining <= {"alembic_version"}

    command.upgrade(config, "head")


def test_migration_is_at_head(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _alembic_config(monkeypatch)
    command.check(config)
