"""Worker duplicate, authorization, retry, cleanup, and reconciliation behavior."""

import asyncio
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.github.client import GitHubClientProtocol
from app.indexing.clone import ClonedRepository, RepositoryClonerProtocol
from app.indexing.discovery import DiscoveryResult, FileDiscovery
from app.indexing.failures import IndexingError
from app.indexing.worker import IndexingWorker
from app.queue import QueueDelivery, WorkerQueueProtocol
from app.services.indexing_jobs import ClaimedJob, IndexingJobStore, JobContext
from tests.conftest import make_settings


def worker_dependencies(
    tmp_path: Path,
) -> tuple[IndexingWorker, Any, Any, Any, Any, Any]:
    settings = make_settings(worker_heartbeat_interval_seconds=0.1)
    queue = MagicMock()
    queue.ensure_group = AsyncMock()
    queue.reclaim = AsyncMock(return_value=())
    queue.receive = AsyncMock(return_value=None)
    queue.acknowledge = AsyncMock()
    queue.enqueue = AsyncMock()
    store = MagicMock()
    store.claim = AsyncMock()
    store.authorized_context = AsyncMock()
    store.heartbeat = AsyncMock()
    store.stage = AsyncMock()
    store.complete = AsyncMock()
    store.cancel_revoked = AsyncMock()
    store.fail = AsyncMock(return_value=False)
    store.recover_abandoned = AsyncMock(return_value=0)
    store.due_jobs = AsyncMock(return_value=())
    store.mark_enqueued = AsyncMock()
    github = MagicMock()
    github.create_installation_token = AsyncMock(return_value=SecretStr("token"))
    cloner = MagicMock()
    cloned = ClonedRepository(
        workspace=tmp_path,
        checkout=tmp_path,
        commit_sha="a" * 40,
    )
    cloner.clone = AsyncMock(return_value=cloned)
    cloner.cleanup = MagicMock()
    discovery = MagicMock()
    result = DiscoveryResult(
        files=(),
        inspected_file_count=1,
        total_bytes=0,
        skipped={"unsupported_type": 1},
    )
    discovery.discover = MagicMock(return_value=result)
    worker = IndexingWorker(
        settings=settings,
        queue=cast(WorkerQueueProtocol, queue),
        store=cast(IndexingJobStore, store),
        github=cast(GitHubClientProtocol, github),
        cloner=cast(RepositoryClonerProtocol, cloner),
        discovery=cast(FileDiscovery, discovery),
        worker_id="test-worker",
    )
    return worker, queue, store, github, cloner, discovery


def claimed_job() -> ClaimedJob:
    return ClaimedJob(id=uuid.uuid4(), repository_id=uuid.uuid4(), attempt=1)


def job_context(claimed: ClaimedJob) -> JobContext:
    return JobContext(
        job_id=claimed.id,
        repository_id=claimed.repository_id,
        github_installation_id=42,
        owner="octo-org",
        name="repo",
        default_branch="main",
    )


@pytest.mark.asyncio
async def test_worker_completes_discovery_and_cleans_clone(tmp_path: Path) -> None:
    worker, queue, store, github, cloner, discovery = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = job_context(claimed)
    delivery = QueueDelivery(delivery_id="1-0", job_id=claimed.id)

    await worker.process_delivery(delivery)

    store.stage.assert_awaited_once()
    store.complete.assert_awaited_once()
    github.create_installation_token.assert_awaited_once_with(42)
    discovery.discover.assert_called_once_with(tmp_path)
    cloner.cleanup.assert_called_once()
    queue.acknowledge.assert_awaited_once_with("1-0")


@pytest.mark.asyncio
async def test_worker_acks_duplicate_without_processing(tmp_path: Path) -> None:
    worker, queue, store, github, cloner, discovery = worker_dependencies(tmp_path)
    store.claim.return_value = None
    delivery = QueueDelivery(delivery_id="duplicate", job_id=uuid.uuid4())

    await worker.process_delivery(delivery)

    queue.acknowledge.assert_awaited_once_with("duplicate")
    github.create_installation_token.assert_not_awaited()
    cloner.clone.assert_not_awaited()
    discovery.discover.assert_not_called()


@pytest.mark.asyncio
async def test_worker_cancels_job_when_durable_access_is_revoked(tmp_path: Path) -> None:
    worker, queue, store, github, cloner, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = None

    await worker.process_delivery(QueueDelivery(delivery_id="revoked", job_id=claimed.id))

    store.cancel_revoked.assert_awaited_once_with(claimed, "test-worker")
    github.create_installation_token.assert_not_awaited()
    cloner.clone.assert_not_awaited()
    queue.acknowledge.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_classifies_retryable_and_unexpected_failures(tmp_path: Path) -> None:
    worker, _, store, _, cloner, _ = worker_dependencies(tmp_path)
    first = claimed_job()
    store.claim.return_value = first
    store.authorized_context.return_value = job_context(first)
    cloner.clone.side_effect = IndexingError(
        code="clone_timeout",
        message="Repository clone timed out",
        retryable=True,
    )

    await worker.process_delivery(QueueDelivery(delivery_id="timeout", job_id=first.id))

    assert store.fail.await_args.kwargs == {
        "code": "clone_timeout",
        "safe_message": "Repository clone timed out",
        "retryable": True,
    }

    second = claimed_job()
    store.claim.return_value = second
    store.authorized_context.return_value = job_context(second)
    cloner.clone.side_effect = RuntimeError("private-repository-path-sentinel")
    await worker.process_delivery(QueueDelivery(delivery_id="internal", job_id=second.id))

    assert store.fail.await_args.kwargs == {
        "code": "worker_internal_error",
        "safe_message": "The indexing worker encountered an internal error",
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_worker_reconciles_abandoned_and_due_jobs(tmp_path: Path) -> None:
    worker, queue, store, _, _, _ = worker_dependencies(tmp_path)
    due = (uuid.uuid4(), uuid.uuid4())
    store.recover_abandoned.return_value = 2
    store.due_jobs.return_value = due

    await worker.reconcile()

    assert [call.args[0] for call in queue.enqueue.await_args_list] == list(due)
    assert [call.args[0] for call in store.mark_enqueued.await_args_list] == list(due)


@pytest.mark.asyncio
async def test_worker_run_initializes_group_and_stops_cleanly(tmp_path: Path) -> None:
    worker, queue, store, _, _, _ = worker_dependencies(tmp_path)

    async def stop_after_receive(_: str) -> QueueDelivery | None:
        worker.stop()
        return cast(QueueDelivery | None, None)

    queue.receive.side_effect = stop_after_receive

    await worker.run()

    queue.ensure_group.assert_awaited_once()
    store.recover_abandoned.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_heartbeat_updates_claimed_job(tmp_path: Path) -> None:
    worker, _, store, _, _, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(worker._heartbeat(claimed, stop))

    await asyncio.sleep(0.12)
    stop.set()
    await heartbeat

    store.heartbeat.assert_awaited_with(claimed, "test-worker")
