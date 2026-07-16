"""Alembic verification against a disposable PostgreSQL database."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
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
