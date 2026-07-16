"""Separate Milestone 3 worker orchestration."""

import asyncio
import os
import socket
import time
import uuid

import structlog

from app.core.config import Settings
from app.db.models.enums import RepositoryIndexingStatus
from app.github.client import GitHubAPIError, GitHubClientProtocol
from app.indexing.clone import CloneRequest, RepositoryClonerProtocol
from app.indexing.discovery import FileDiscovery
from app.indexing.failures import IndexingError
from app.queue import QueueDelivery, QueueUnavailableError, WorkerQueueProtocol
from app.services.indexing_jobs import ClaimedJob, IndexingJobStore

logger = structlog.get_logger(__name__)


class IndexingWorker:
    """Consume opaque identifiers and re-authorize every durable job."""

    def __init__(
        self,
        *,
        settings: Settings,
        queue: WorkerQueueProtocol,
        store: IndexingJobStore,
        github: GitHubClientProtocol,
        cloner: RepositoryClonerProtocol,
        discovery: FileDiscovery,
        worker_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._store = store
        self._github = github
        self._cloner = cloner
        self._discovery = discovery
        self._worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        await self._queue.ensure_group()
        await self.reconcile()
        last_reconcile = time.monotonic()
        logger.info("worker_started")
        try:
            while not self._stop.is_set():
                try:
                    for reclaimed_delivery in await self._queue.reclaim(self._worker_id):
                        await self.process_delivery(reclaimed_delivery)
                    new_delivery = await self._queue.receive(self._worker_id)
                    if new_delivery is not None:
                        await self.process_delivery(new_delivery)
                    if (
                        time.monotonic() - last_reconcile
                        >= self._settings.worker_reconcile_interval_seconds
                    ):
                        await self.reconcile()
                        last_reconcile = time.monotonic()
                except QueueUnavailableError as error:
                    logger.warning("worker_queue_unavailable", error_type=type(error).__name__)
                    await asyncio.sleep(1)
        finally:
            logger.info("worker_stopped")

    async def reconcile(self) -> None:
        recovered = await self._store.recover_abandoned()
        if recovered:
            logger.warning("abandoned_jobs_recovered", count=recovered)
        for job_id in await self._store.due_jobs():
            await self._queue.enqueue(job_id)
            await self._store.mark_enqueued(job_id)

    async def process_delivery(self, delivery: QueueDelivery) -> None:
        claimed = await self._store.claim(delivery.job_id, self._worker_id)
        if claimed is None:
            await self._queue.acknowledge(delivery.delivery_id)
            return
        logger.info(
            "indexing_job_claimed",
            job_id=str(claimed.id),
            repository_id=str(claimed.repository_id),
            attempt=claimed.attempt,
        )
        try:
            await self._run_claimed(claimed)
        finally:
            await self._queue.acknowledge(delivery.delivery_id)

    async def _run_claimed(self, claimed: ClaimedJob) -> None:
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(claimed, heartbeat_stop))
        try:
            context = await self._store.authorized_context(claimed)
            if context is None:
                await self._store.cancel_revoked(claimed, self._worker_id)
                logger.warning(
                    "indexing_job_access_revoked",
                    job_id=str(claimed.id),
                    repository_id=str(claimed.repository_id),
                )
                return
            try:
                token = await self._github.create_installation_token(context.github_installation_id)
            except GitHubAPIError as error:
                raise IndexingError(
                    code="github_token_unavailable",
                    message="GitHub repository access is temporarily unavailable",
                    retryable=True,
                ) from error

            cloned = None
            try:
                cloned = await self._cloner.clone(
                    CloneRequest(
                        owner=context.owner,
                        name=context.name,
                        default_branch=context.default_branch,
                        installation_token=token,
                    )
                )
                await self._store.stage(
                    claimed,
                    self._worker_id,
                    status=RepositoryIndexingStatus.DISCOVERING,
                    stage="discovering",
                    progress=55,
                    commit_sha=cloned.commit_sha,
                )
                discovery = await asyncio.to_thread(self._discovery.discover, cloned.checkout)
                await self._store.complete(
                    claimed,
                    self._worker_id,
                    commit_sha=cloned.commit_sha,
                    discovery=discovery,
                )
                logger.info(
                    "indexing_job_completed",
                    job_id=str(claimed.id),
                    repository_id=str(claimed.repository_id),
                    discovered_file_count=len(discovery.files),
                    skipped_file_count=sum(discovery.skipped.values()),
                )
            finally:
                if cloned is not None:
                    self._cloner.cleanup(cloned)
        except IndexingError as error:
            retrying = await self._store.fail(
                claimed,
                self._worker_id,
                code=error.code,
                safe_message=error.safe_message,
                retryable=error.retryable,
            )
            logger.warning(
                "indexing_job_failed",
                job_id=str(claimed.id),
                repository_id=str(claimed.repository_id),
                error_code=error.code,
                retrying=retrying,
            )
        except Exception as error:  # noqa: BLE001
            retrying = await self._store.fail(
                claimed,
                self._worker_id,
                code="worker_internal_error",
                safe_message="The indexing worker encountered an internal error",
                retryable=True,
            )
            logger.error(  # noqa: TRY400 -- tracebacks may contain private clone paths
                "indexing_job_internal_error",
                job_id=str(claimed.id),
                repository_id=str(claimed.repository_id),
                error_type=type(error).__name__,
                retrying=retrying,
            )
        finally:
            heartbeat_stop.set()
            await heartbeat

    async def _heartbeat(self, claimed: ClaimedJob, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._settings.worker_heartbeat_interval_seconds,
                )
            except TimeoutError:
                await self._store.heartbeat(claimed, self._worker_id)
