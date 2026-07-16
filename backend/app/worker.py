"""Executable entry point for the private indexing worker process."""

import asyncio
from contextlib import suppress

import structlog

from app.core.config import load_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.github.client import GitHubClient
from app.indexing.analyzer import ProcessIsolatedAnalyzer
from app.indexing.clone import GitHubRepositoryCloner
from app.indexing.discovery import FileDiscovery
from app.indexing.worker import IndexingWorker
from app.queue import RedisJobQueue
from app.services.indexing_jobs import IndexingJobStore

logger = structlog.get_logger(__name__)


async def run_worker() -> None:
    settings = load_settings()
    configure_logging(level=settings.log_level, render_json=settings.log_json)
    database = Database.from_settings(settings)
    github = GitHubClient(settings)
    queue = RedisJobQueue.from_settings(settings)
    worker = IndexingWorker(
        settings=settings,
        queue=queue,
        store=IndexingJobStore(database, settings),
        github=github,
        cloner=GitHubRepositoryCloner(settings),
        discovery=FileDiscovery(settings),
        analyzer=ProcessIsolatedAnalyzer(settings),
    )
    logger.info("worker_configuration_loaded", **settings.safe_summary())
    try:
        await worker.run()
    finally:
        await queue.close()
        await github.close()
        await database.dispose()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
