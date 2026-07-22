"""Executable entry point for the private indexing worker process."""

import asyncio
import signal
from contextlib import suppress

import structlog

from app.core.config import load_settings
from app.core.logging import configure_logging
from app.db.session import Database
from app.embeddings.client import EmbeddingServiceClient
from app.github.client import GitHubClient
from app.indexing.analyzer import ProcessIsolatedAnalyzer
from app.indexing.clone import GitHubRepositoryCloner
from app.indexing.discovery import FileDiscovery
from app.indexing.worker import IndexingWorker
from app.queue import RedisJobQueue
from app.services.indexing_jobs import IndexingJobStore
from app.vector.qdrant import QdrantVectorStore

logger = structlog.get_logger(__name__)


def _install_shutdown_handlers(
    loop: asyncio.AbstractEventLoop,
    worker: IndexingWorker,
) -> tuple[signal.Signals, ...]:
    """Stop new deliveries on process termination while the active job drains."""
    installed: list[signal.Signals] = []
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(shutdown_signal, worker.stop)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(shutdown_signal)
    return tuple(installed)


def _remove_shutdown_handlers(
    loop: asyncio.AbstractEventLoop,
    installed: tuple[signal.Signals, ...],
) -> None:
    for shutdown_signal in installed:
        loop.remove_signal_handler(shutdown_signal)


async def run_worker() -> None:
    settings = load_settings()
    configure_logging(level=settings.log_level, render_json=settings.log_json)
    database = Database.from_settings(settings)
    github = GitHubClient(settings)
    queue = RedisJobQueue.from_settings(settings)
    embeddings = EmbeddingServiceClient(settings)
    vectors = QdrantVectorStore(settings)
    worker = IndexingWorker(
        settings=settings,
        queue=queue,
        store=IndexingJobStore(database, settings),
        github=github,
        cloner=GitHubRepositoryCloner(settings),
        discovery=FileDiscovery(settings),
        analyzer=ProcessIsolatedAnalyzer(settings),
        embeddings=embeddings,
        vectors=vectors,
    )
    loop = asyncio.get_running_loop()
    installed_handlers = _install_shutdown_handlers(loop, worker)
    logger.info("worker_configuration_loaded", **settings.safe_summary())
    try:
        await worker.run()
    finally:
        _remove_shutdown_handlers(loop, installed_handlers)
        await queue.close()
        await embeddings.close()
        await vectors.close()
        await github.close()
        await database.dispose()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
