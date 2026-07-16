"""Async session lifecycle and PostgreSQL constraints."""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models.user import User
from app.db.session import Database

pytestmark = pytest.mark.integration


def _test_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.fail("TEST_DATABASE_URL must target a disposable PostgreSQL database")
    return value


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    engine = create_async_engine(_test_database_url(), pool_pre_ping=True)
    database = Database(engine=engine, ready_timeout_seconds=2)
    try:
        yield database
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(User))
        await database.dispose()


@pytest.mark.asyncio
async def test_database_readiness_executes_real_postgresql_probe(database: Database) -> None:
    assert await database.is_ready() is True


@pytest.mark.asyncio
async def test_session_rolls_back_failed_unit_of_work(database: Database) -> None:
    with pytest.raises(RuntimeError):
        async with database.session() as session:
            session.add(User(github_user_id=1001, github_login="rollback-user"))
            await session.flush()
            raise RuntimeError("force rollback")

    async with database.session() as session:
        count = await session.scalar(select(func.count()).select_from(User))
    assert count == 0


@pytest.mark.asyncio
async def test_unique_github_user_id_is_enforced(database: Database) -> None:
    async with database.session() as session:
        session.add(User(github_user_id=2002, github_login="first"))
        await session.commit()

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            session.add(User(github_user_id=2002, github_login="second"))
            await session.commit()
