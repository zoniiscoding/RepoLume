"""Async SQLAlchemy engine and short-lived session lifecycle."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

logger = structlog.get_logger(__name__)


class DatabaseProtocol(Protocol):
    """Minimum database lifecycle used by the application."""

    async def is_ready(self) -> bool:
        """Return whether the database can execute a bounded probe."""
        ...

    async def dispose(self) -> None:
        """Release engine resources."""
        ...


class Database:
    """Own the async engine and produce transaction-bounded sessions."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        ready_timeout_seconds: float,
    ) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        self._ready_timeout_seconds = ready_timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        """Construct the production database adapter from validated settings."""
        engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
        )
        return cls(
            engine=engine,
            ready_timeout_seconds=settings.database_ready_timeout_seconds,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield one short-lived session and rollback failed units of work."""
        async with self.session_factory() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    async def is_ready(self) -> bool:
        """Execute a bounded PostgreSQL probe and redact connection failures."""
        try:
            async with asyncio.timeout(self._ready_timeout_seconds):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
        except (TimeoutError, OSError, SQLAlchemyError) as error:
            logger.warning("database_readiness_failed", error_type=type(error).__name__)
            return False
        else:
            return True

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()
