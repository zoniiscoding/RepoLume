"""Worker duplicate, authorization, retry, cleanup, and reconciliation behavior."""

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.db.models.enums import IndexingMode, RepositoryAccessMode
from app.embeddings.client import EmbeddingProviderProtocol
from app.github.client import (
    GitHubAPIError,
    GitHubClientProtocol,
    GitHubRepositoryPrivateError,
    PublicGitHubRepository,
)
from app.github.schemas import GitHubCommitComparison, GitHubRepository
from app.indexing.analyzer import RepositoryAnalyzerProtocol
from app.indexing.clone import ClonedRepository, RepositoryClonerProtocol
from app.indexing.discovery import DiscoveredFile, DiscoveryResult, FileDiscovery
from app.indexing.failures import IndexingError
from app.indexing.models import ChunkType, ContentChunk, ProcessingResult
from app.indexing.worker import IndexingWorker
from app.queue import QueueDelivery, WorkerQueueProtocol
from app.services.indexing_jobs import ClaimedJob, IndexingJobStore, JobContext
from app.vector.qdrant import VectorStoreProtocol
from tests.conftest import make_settings


def empty_processing_result() -> ProcessingResult:
    return ProcessingResult(
        repository_id=uuid.UUID(int=0),
        index_version=1,
        commit_sha="a" * 40,
        parsed_file_count=0,
        partial_file_count=0,
        skipped_file_count=0,
        symbol_count=0,
        chunk_count=0,
        warning_counts={},
        symbols=(),
        chunk_fingerprints=(),
        chunks=(),
    )


async def analyze_empty(**kwargs: Any) -> ProcessingResult:
    await kwargs["on_chunking"]()
    await kwargs["on_graphing"]()
    return empty_processing_result()


def worker_dependencies(  # noqa: PLR0915 -- explicit protocol doubles document the boundary
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
    store.is_current = AsyncMock(return_value=True)
    store.mark_stale = AsyncMock()
    store.record_freshness_plan = AsyncMock()
    store.heartbeat = AsyncMock()
    store.stage = AsyncMock()
    store.prepare_build = AsyncMock()
    store.record_vector_counts = AsyncMock()
    store.validate_graph = AsyncMock()
    store.mark_build_ready = AsyncMock()
    store.activate = AsyncMock(return_value=0)
    store.can_cleanup_inactive = AsyncMock(return_value=True)
    store.record_failed_build_cleanup = AsyncMock()
    store.complete_superseded_cleanup = AsyncMock()
    store.cancel_revoked = AsyncMock()
    store.revoke_public_access = AsyncMock()
    store.fail = AsyncMock(return_value=False)
    store.recover_abandoned = AsyncMock(return_value=0)
    store.due_jobs = AsyncMock(return_value=())
    store.mark_enqueued = AsyncMock()
    github = MagicMock()
    github.create_repository_installation_token = AsyncMock(return_value=SecretStr("token"))
    github.compare_repository_commits = AsyncMock()
    github.get_public_repository = AsyncMock()
    github.compare_public_repository_commits = AsyncMock()
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
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(side_effect=analyze_empty)
    embeddings = MagicMock()
    embeddings.is_ready = AsyncMock(return_value=True)
    embeddings.embed_documents = AsyncMock(return_value={})
    embeddings.embed_query = AsyncMock()
    embeddings.close = AsyncMock()
    vectors = MagicMock()
    vectors.is_ready = AsyncMock(return_value=True)
    vectors.ensure_collection = AsyncMock()
    vectors.delete_scope = AsyncMock()
    vectors.upsert = AsyncMock()
    vectors.validate_scope = AsyncMock()
    vectors.count_scope = AsyncMock(return_value=0)
    vectors.reusable_vectors = AsyncMock(return_value={})
    vectors.close = AsyncMock()
    worker = IndexingWorker(
        settings=settings,
        queue=cast(WorkerQueueProtocol, queue),
        store=cast(IndexingJobStore, store),
        github=cast(GitHubClientProtocol, github),
        cloner=cast(RepositoryClonerProtocol, cloner),
        discovery=cast(FileDiscovery, discovery),
        analyzer=cast(RepositoryAnalyzerProtocol, analyzer),
        embeddings=cast(EmbeddingProviderProtocol, embeddings),
        vectors=cast(VectorStoreProtocol, vectors),
        worker_id="test-worker",
    )
    return worker, queue, store, github, cloner, discovery


def claimed_job() -> ClaimedJob:
    return ClaimedJob(id=uuid.uuid4(), repository_id=uuid.uuid4(), attempt=1)


def job_context(claimed: ClaimedJob) -> JobContext:
    return JobContext(
        job_id=claimed.id,
        repository_id=claimed.repository_id,
        installation_id=uuid.uuid4(),
        github_installation_id=42,
        owner="octo-org",
        name="repo",
        default_branch="main",
        index_version=1,
    )


def public_job_context(claimed: ClaimedJob) -> JobContext:
    return JobContext(
        job_id=claimed.id,
        repository_id=claimed.repository_id,
        installation_id=None,
        github_installation_id=None,
        owner="octo-org",
        name="repo",
        default_branch="main",
        index_version=1,
        github_repository_id=9001,
        access_mode=RepositoryAccessMode.PUBLIC,
    )


def public_metadata() -> PublicGitHubRepository:
    return PublicGitHubRepository(
        repository=GitHubRepository.model_validate(
            {
                "id": 9001,
                "owner": {"login": "octo-org"},
                "name": "repo",
                "full_name": "octo-org/repo",
                "html_url": "https://github.com/octo-org/repo",
                "private": False,
                "default_branch": "main",
            }
        ),
        default_branch_sha="a" * 40,
    )


@pytest.mark.asyncio
async def test_worker_completes_discovery_and_cleans_clone(tmp_path: Path) -> None:
    worker, queue, store, github, cloner, discovery = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = job_context(claimed)
    delivery = QueueDelivery(delivery_id="1-0", job_id=claimed.id)

    async def activate_after_cleanup(*args: object, **kwargs: object) -> int:
        del args, kwargs
        assert cloner.cleanup.called
        return 0

    store.activate.side_effect = activate_after_cleanup

    await worker.process_delivery(delivery)

    assert store.stage.await_count == 8
    assert [call.kwargs["stage"] for call in store.stage.await_args_list] == [
        "discovering",
        "parsing",
        "chunking",
        "building_graph",
        "embedding",
        "storing_vectors",
        "validating_index",
        "activating_index",
    ]
    assert [call.kwargs["progress"] for call in store.stage.await_args_list] == [
        55,
        65,
        85,
        87,
        88,
        92,
        96,
        99,
    ]
    store.prepare_build.assert_awaited_once()
    store.validate_graph.assert_awaited_once()
    store.activate.assert_awaited_once()
    github.create_repository_installation_token.assert_awaited_once()
    discovery.discover.assert_called_once_with(tmp_path)
    cloner.cleanup.assert_called_once()
    queue.acknowledge.assert_awaited_once_with("1-0")


@pytest.mark.asyncio
async def test_worker_does_not_activate_or_mark_terminal_before_clone_cleanup(
    tmp_path: Path,
) -> None:
    worker, queue, store, _, cloner, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = job_context(claimed)
    cloner.cleanup.side_effect = IndexingError(
        code="clone_cleanup_failed",
        message="Repository clone cleanup failed",
        retryable=True,
    )

    await worker.process_delivery(QueueDelivery(delivery_id="cleanup-failed", job_id=claimed.id))

    store.activate.assert_not_awaited()
    store.mark_stale.assert_not_awaited()
    assert store.fail.await_args.kwargs == {
        "code": "clone_cleanup_failed",
        "safe_message": "Repository clone cleanup failed",
        "retryable": True,
    }
    queue.acknowledge.assert_awaited_once_with("cleanup-failed")


@pytest.mark.asyncio
async def test_public_worker_revalidates_identity_and_clones_without_installation_token(
    tmp_path: Path,
) -> None:
    worker, queue, store, github, cloner, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = public_job_context(claimed)
    github.get_public_repository.return_value = public_metadata()

    await worker.process_delivery(QueueDelivery(delivery_id="public", job_id=claimed.id))

    github.get_public_repository.assert_awaited_once_with(owner="octo-org", repository="repo")
    github.create_repository_installation_token.assert_not_awaited()
    assert cloner.clone.await_args.args[0].installation_token is None
    store.activate.assert_awaited_once()
    queue.acknowledge.assert_awaited_once_with("public")


@pytest.mark.asyncio
async def test_public_worker_revokes_repository_that_is_no_longer_public(tmp_path: Path) -> None:
    worker, queue, store, github, cloner, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = public_job_context(claimed)
    github.get_public_repository.side_effect = GitHubRepositoryPrivateError

    await worker.process_delivery(QueueDelivery(delivery_id="public-private", job_id=claimed.id))

    store.revoke_public_access.assert_awaited_once_with(claimed.repository_id)
    store.fail.assert_awaited_once()
    assert store.fail.await_args.kwargs == {
        "code": "public_repository_unavailable",
        "safe_message": "The public repository is no longer available",
        "retryable": False,
    }
    cloner.clone.assert_not_awaited()
    queue.acknowledge.assert_awaited_once_with("public-private")


@pytest.mark.parametrize("complete_previous_artifacts", [True, False])
@pytest.mark.asyncio
async def test_incremental_worker_reuses_only_complete_unchanged_vector_state(
    tmp_path: Path,
    complete_previous_artifacts: bool,
) -> None:
    worker, queue, store, github, cloner, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    context = JobContext(
        job_id=claimed.id,
        repository_id=claimed.repository_id,
        installation_id=uuid.uuid4(),
        github_installation_id=42,
        owner="octo-org",
        name="repo",
        default_branch="main",
        index_version=2,
        github_repository_id=9001,
        refresh_generation=1,
        active_index_version=1,
        active_commit_sha="a" * 40,
        indexed_branch="main",
        requested_mode=IndexingMode.INCREMENTAL,
    )
    store.claim.return_value = claimed
    store.authorized_context.return_value = context
    cloner.clone.return_value = ClonedRepository(
        workspace=tmp_path,
        checkout=tmp_path,
        commit_sha="b" * 40,
    )
    github.compare_repository_commits.return_value = GitHubCommitComparison.model_validate(
        {
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "total_commits": 1,
            "files": [{"filename": "changed.py", "status": "modified", "changes": 2}],
        }
    )
    chunks = tuple(
        ContentChunk(
            repository_id=claimed.repository_id,
            index_version=2,
            ordinal=ordinal,
            file_path=path,
            language="python",
            chunk_type=ChunkType.MODULE,
            symbol_name=None,
            qualified_name=path.removesuffix(".py"),
            parent_qualified_name=None,
            heading_hierarchy=(),
            imports=(),
            decorators=(),
            signature=None,
            docstring=None,
            start_line=1,
            end_line=1,
            commit_sha="b" * 40,
            content_hash=("c" if ordinal == 0 else "d") * 64,
            content=f"value = {ordinal}",
        )
        for ordinal, path in enumerate(("unchanged.py", "changed.py"))
    )
    processing = ProcessingResult(
        repository_id=claimed.repository_id,
        index_version=2,
        commit_sha="b" * 40,
        parsed_file_count=2,
        partial_file_count=0,
        skipped_file_count=0,
        symbol_count=0,
        chunk_count=2,
        warning_counts={},
        symbols=(),
        chunk_fingerprints=(),
        chunks=chunks,
    )
    worker._analyzer.analyze.side_effect = None  # type: ignore[attr-defined]
    worker._analyzer.analyze.return_value = processing  # type: ignore[attr-defined]
    worker._vectors.reusable_vectors.return_value = (  # type: ignore[attr-defined]
        {"0": (1.0,) + (0.0,) * 767} if complete_previous_artifacts else {}
    )
    worker._embeddings.embed_documents.return_value = dict.fromkeys(  # type: ignore[attr-defined]
        ("1",) if complete_previous_artifacts else ("0", "1"),
        (1.0,) + (0.0,) * 767,
    )

    await worker.process_delivery(QueueDelivery(delivery_id="2-0", job_id=claimed.id))

    prepared_batch = worker._embeddings.embed_documents.await_args.args[0]  # type: ignore[attr-defined]
    assert [item.item_id for item in prepared_batch] == (
        ["1"] if complete_previous_artifacts else ["0", "1"]
    )
    assert store.record_vector_counts.await_args.kwargs == {
        "embedded_chunk_count": 2,
        "vector_count": 2,
        "reused_chunk_count": 1 if complete_previous_artifacts else 0,
        "reembedded_chunk_count": 1 if complete_previous_artifacts else 2,
    }
    if not complete_previous_artifacts:
        assert store.record_freshness_plan.await_args.kwargs["actual_mode"] is IndexingMode.FULL
        assert (
            store.record_freshness_plan.await_args.kwargs["fallback_reason"]
            == "previous_artifact_missing"
        )
    queue.acknowledge.assert_awaited_once_with("2-0")


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        ("comparison_unavailable", "comparison_unavailable"),
        ("changed_bytes", "changed_bytes_limit"),
    ],
)
@pytest.mark.asyncio
async def test_worker_falls_back_to_full_when_delta_cannot_be_reused_safely(
    tmp_path: Path,
    scenario: str,
    expected_reason: str,
) -> None:
    worker, queue, store, github, cloner, discovery = worker_dependencies(tmp_path)
    claimed = claimed_job()
    context = JobContext(
        job_id=claimed.id,
        repository_id=claimed.repository_id,
        installation_id=uuid.uuid4(),
        github_installation_id=42,
        owner="octo-org",
        name="repo",
        default_branch="main",
        index_version=2,
        github_repository_id=9001,
        refresh_generation=1,
        active_index_version=1,
        active_commit_sha="a" * 40,
        indexed_branch="main",
        requested_mode=IndexingMode.INCREMENTAL,
    )
    store.claim.return_value = claimed
    store.authorized_context.return_value = context
    cloner.clone.return_value = ClonedRepository(
        workspace=tmp_path,
        checkout=tmp_path,
        commit_sha="b" * 40,
    )
    if scenario == "comparison_unavailable":
        github.compare_repository_commits.side_effect = GitHubAPIError
    else:
        github.compare_repository_commits.return_value = GitHubCommitComparison.model_validate(
            {
                "status": "ahead",
                "ahead_by": 1,
                "behind_by": 0,
                "total_commits": 1,
                "files": [{"filename": "changed.py", "status": "modified"}],
            }
        )
        discovery.discover.return_value = DiscoveryResult(
            files=(DiscoveredFile("changed.py", 64 * 1024 * 1024 + 1),),
            inspected_file_count=1,
            total_bytes=64 * 1024 * 1024 + 1,
            skipped={},
        )

    await worker.process_delivery(QueueDelivery(delivery_id=scenario, job_id=claimed.id))

    assert store.record_freshness_plan.await_args.kwargs["actual_mode"] is IndexingMode.FULL
    assert store.record_freshness_plan.await_args.kwargs["fallback_reason"] == expected_reason
    worker._vectors.reusable_vectors.assert_not_awaited()  # type: ignore[attr-defined]
    queue.acknowledge.assert_awaited_once_with(scenario)


@pytest.mark.asyncio
async def test_worker_marks_an_incremental_target_that_is_already_active_stale(
    tmp_path: Path,
) -> None:
    worker, queue, store, _, _, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    context = replace(
        job_context(claimed),
        active_index_version=1,
        active_commit_sha="a" * 40,
        indexed_branch="main",
        requested_mode=IndexingMode.INCREMENTAL,
    )
    store.claim.return_value = claimed
    store.authorized_context.return_value = context

    await worker.process_delivery(QueueDelivery(delivery_id="already-active", job_id=claimed.id))

    store.mark_stale.assert_awaited_once_with(
        claimed,
        "test-worker",
        code="target_already_active",
    )
    worker._analyzer.analyze.assert_not_awaited()  # type: ignore[attr-defined]
    queue.acknowledge.assert_awaited_once_with("already-active")


@pytest.mark.asyncio
async def test_worker_acks_duplicate_without_processing(tmp_path: Path) -> None:
    worker, queue, store, github, cloner, discovery = worker_dependencies(tmp_path)
    store.claim.return_value = None
    delivery = QueueDelivery(delivery_id="duplicate", job_id=uuid.uuid4())

    await worker.process_delivery(delivery)

    queue.acknowledge.assert_awaited_once_with("duplicate")
    github.create_repository_installation_token.assert_not_awaited()
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
    github.create_repository_installation_token.assert_not_awaited()
    cloner.clone.assert_not_awaited()
    queue.acknowledge.assert_awaited_once()


@pytest.mark.asyncio
async def test_newer_generation_blocks_old_worker_immediately_before_activation(
    tmp_path: Path,
) -> None:
    worker, queue, store, _, _, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    store.claim.return_value = claimed
    store.authorized_context.return_value = job_context(claimed)
    store.is_current.side_effect = [True, False]

    await worker.process_delivery(QueueDelivery(delivery_id="stale-worker", job_id=claimed.id))

    store.mark_build_ready.assert_awaited_once()
    store.activate.assert_not_awaited()
    store.mark_stale.assert_awaited_once_with(
        claimed,
        "test-worker",
        code="refresh_superseded_before_activation",
        superseded=True,
    )
    assert worker._vectors.delete_scope.await_count == 2  # type: ignore[attr-defined]
    queue.acknowledge.assert_awaited_once_with("stale-worker")


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


@pytest.mark.parametrize(
    ("failure_method", "error_code", "retryable"),
    [
        ("upsert", "qdrant_partial_write_failed", True),
        ("validate_scope", "vector_count_mismatch", False),
        ("validate_graph", "call_graph_validation_failed", False),
        ("mark_build_ready", "vector_count_mismatch", False),
        ("activate", "index_activation_race", False),
    ],
)
@pytest.mark.asyncio
async def test_worker_cleans_failed_inactive_index_without_activation(
    tmp_path: Path,
    failure_method: str,
    error_code: str,
    retryable: bool,
) -> None:
    worker, queue, store, _, cloner, _ = worker_dependencies(tmp_path)
    claimed = claimed_job()
    context = job_context(claimed)
    store.claim.return_value = claimed
    store.authorized_context.return_value = context
    dependency = (
        store
        if failure_method in {"validate_graph", "mark_build_ready", "activate"}
        else worker._vectors
    )
    getattr(dependency, failure_method).side_effect = IndexingError(
        code=error_code,
        message="The inactive index failed safely",
        retryable=retryable,
    )

    await worker.process_delivery(
        QueueDelivery(delivery_id=f"failed-{failure_method}", job_id=claimed.id)
    )

    assert store.fail.await_args.kwargs == {
        "code": error_code,
        "safe_message": "The inactive index failed safely",
        "retryable": retryable,
    }
    if failure_method == "activate":
        store.activate.assert_awaited_once()
    else:
        store.activate.assert_not_awaited()
    assert cast(Any, worker._vectors.delete_scope).await_count == 2
    store.record_failed_build_cleanup.assert_awaited_once_with(
        context.repository_id,
        context.index_version,
        cleanup_complete=True,
    )
    cloner.cleanup.assert_called_once()
    queue.acknowledge.assert_awaited_once()


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
