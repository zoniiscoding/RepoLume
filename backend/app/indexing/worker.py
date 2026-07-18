"""Separate worker orchestration for safe repository indexing and activation."""

import asyncio
import os
import socket
import time
import uuid
from dataclasses import dataclass

import structlog
from pydantic import SecretStr

from app.core.config import Settings
from app.db.models.enums import IndexingMode, RepositoryIndexingStatus
from app.embeddings.client import EmbeddingProviderProtocol
from app.embeddings.preprocessing import EmbeddingPreprocessor
from app.github.client import GitHubAPIError, GitHubClientProtocol
from app.indexing.analyzer import RepositoryAnalyzerProtocol
from app.indexing.clone import ClonedRepository, CloneRequest, RepositoryClonerProtocol
from app.indexing.discovery import DiscoveryResult, FileDiscovery
from app.indexing.failures import IndexingError
from app.indexing.freshness import FreshnessPlan, plan_refresh
from app.indexing.models import ProcessingResult
from app.queue import QueueDelivery, QueueUnavailableError, WorkerQueueProtocol
from app.services.indexing_jobs import ClaimedJob, IndexingJobStore, JobContext
from app.vector.qdrant import (
    VectorScope,
    VectorStoreProtocol,
    build_vector_record,
    embedding_model_fingerprint,
)

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class _RunState:
    context: JobContext | None = None
    build_prepared: bool = False


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
        analyzer: RepositoryAnalyzerProtocol,
        embeddings: EmbeddingProviderProtocol,
        vectors: VectorStoreProtocol,
        worker_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._store = store
        self._github = github
        self._cloner = cloner
        self._discovery = discovery
        self._analyzer = analyzer
        self._embeddings = embeddings
        self._vectors = vectors
        self._preprocessor = EmbeddingPreprocessor(settings)
        self._worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if not await self._embeddings.is_ready():
            raise IndexingError(
                code="embedding_model_not_ready",
                message="The embedding model is not ready",
                retryable=True,
            )
        await self._vectors.ensure_collection()
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
        state = _RunState()
        try:
            state.context = await self._store.authorized_context(claimed)
            if state.context is None:
                await self._store.cancel_revoked(claimed, self._worker_id)
                logger.warning(
                    "indexing_job_access_revoked",
                    job_id=str(claimed.id),
                    repository_id=str(claimed.repository_id),
                )
                return
            if not await self._store.is_current(claimed, self._worker_id):
                await self._store.mark_stale(
                    claimed,
                    self._worker_id,
                    code="refresh_superseded",
                    superseded=True,
                )
                return
            await self._process_authorized(claimed, state)
        except IndexingError as error:
            cleanup_complete = True
            if state.context is not None and state.build_prepared:
                cleanup_complete = await self._cleanup_inactive(state.context)
            retrying = await self._store.fail(
                claimed,
                self._worker_id,
                code=error.code,
                safe_message=error.safe_message,
                retryable=error.retryable or not cleanup_complete,
            )
            logger.warning(
                "indexing_job_failed",
                job_id=str(claimed.id),
                repository_id=str(claimed.repository_id),
                error_code=error.code,
                retrying=retrying,
            )
        except Exception as error:  # noqa: BLE001
            if state.context is not None and state.build_prepared:
                await self._cleanup_inactive(state.context)
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

    async def _process_authorized(self, claimed: ClaimedJob, state: _RunState) -> None:
        context = state.context
        if context is None:
            raise AssertionError("missing_context")
        token = await self._installation_token(context)
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
            if (
                context.active_commit_sha == cloned.commit_sha
                and context.indexed_branch == context.default_branch
                and context.requested_mode is IndexingMode.INCREMENTAL
            ):
                await self._store.mark_stale(
                    claimed,
                    self._worker_id,
                    code="target_already_active",
                )
                return
            comparison = None
            if context.active_commit_sha is not None:
                try:
                    comparison = await self._github.compare_repository_commits(
                        token,
                        owner=context.owner,
                        repository=context.name,
                        base=context.active_commit_sha,
                        head=cloned.commit_sha,
                    )
                except GitHubAPIError:
                    comparison = None
            plan = plan_refresh(
                comparison,
                has_active_index=context.active_index_version > 0,
                requested_mode=context.requested_mode,
                max_changed_files=self._settings.freshness_max_changed_files,
            )
            discovery, processing = await self._analyze(claimed, context, cloned)
            if plan.actual_mode is IndexingMode.INCREMENTAL:
                changed_bytes = sum(
                    item.size_bytes
                    for item in discovery.files
                    if item.relative_path in plan.changes.target_paths
                )
                if changed_bytes > self._settings.freshness_max_changed_bytes:
                    plan = FreshnessPlan(
                        actual_mode=IndexingMode.FULL,
                        fallback_reason="changed_bytes_limit",
                        changes=plan.changes,
                    )
            await self._store.record_freshness_plan(
                claimed,
                self._worker_id,
                actual_mode=plan.actual_mode,
                fallback_reason=plan.fallback_reason,
                changed_counts=plan.changes.counts,
                changed_file_count=len(plan.changes.target_paths | plan.changes.removed_paths),
            )
            await self._store.prepare_build(
                claimed,
                self._worker_id,
                commit_sha=cloned.commit_sha,
                discovery=discovery,
                processing=processing,
                preprocessing_fingerprint=self._preprocessor.policy_fingerprint,
            )
            state.build_prepared = True
            vector_count = await self._embed_and_store(claimed, context, cloned, processing, plan)
            if await self._validate_and_activate(claimed, context, cloned, vector_count):
                self._log_completion(claimed, context, discovery, processing, vector_count)
        finally:
            if cloned is not None:
                self._cloner.cleanup(cloned)

    async def _installation_token(self, context: JobContext) -> SecretStr:
        try:
            return await self._github.create_repository_installation_token(
                context.github_installation_id,
                repository_id=context.github_repository_id,
            )
        except GitHubAPIError as error:
            raise IndexingError(
                code="github_token_unavailable",
                message="GitHub repository access is temporarily unavailable",
                retryable=True,
            ) from error

    async def _analyze(
        self,
        claimed: ClaimedJob,
        context: JobContext,
        cloned: ClonedRepository,
    ) -> tuple[DiscoveryResult, ProcessingResult]:
        await self._store.stage(
            claimed,
            self._worker_id,
            status=RepositoryIndexingStatus.DISCOVERING,
            stage="discovering",
            progress=55,
            commit_sha=cloned.commit_sha,
        )
        discovery = await asyncio.to_thread(self._discovery.discover, cloned.checkout)
        await self._store.stage(
            claimed,
            self._worker_id,
            status=RepositoryIndexingStatus.PARSING,
            stage="parsing",
            progress=65,
            commit_sha=cloned.commit_sha,
        )

        async def mark_chunking() -> None:
            await self._store.stage(
                claimed,
                self._worker_id,
                status=RepositoryIndexingStatus.PARSING,
                stage="chunking",
                progress=85,
                commit_sha=cloned.commit_sha,
            )

        async def mark_graphing() -> None:
            await self._store.stage(
                claimed,
                self._worker_id,
                status=RepositoryIndexingStatus.BUILDING_GRAPH,
                stage="building_graph",
                progress=87,
                commit_sha=cloned.commit_sha,
            )

        processing = await self._analyzer.analyze(
            checkout=cloned.checkout,
            discovery=discovery,
            repository_id=context.repository_id,
            index_version=context.index_version,
            commit_sha=cloned.commit_sha,
            on_chunking=mark_chunking,
            on_graphing=mark_graphing,
        )
        return discovery, processing

    async def _embed_and_store(
        self,
        claimed: ClaimedJob,
        context: JobContext,
        cloned: ClonedRepository,
        processing: ProcessingResult,
        plan: FreshnessPlan,
    ) -> int:
        await self._store.stage(
            claimed,
            self._worker_id,
            status=RepositoryIndexingStatus.EMBEDDING,
            stage="embedding",
            progress=88,
            commit_sha=cloned.commit_sha,
        )
        prepared = tuple(self._preprocessor.prepare_chunk(chunk) for chunk in processing.chunks)
        reused: dict[str, tuple[float, ...]] = {}
        if plan.actual_mode is IndexingMode.INCREMENTAL and context.active_index_version > 0:
            reusable = tuple(
                document
                for document in prepared
                if document.chunk is not None
                and document.chunk.file_path not in plan.changes.target_paths
            )
            reused = await self._vectors.reusable_vectors(
                self._scope(context, context.active_index_version),
                prepared=reusable,
                commit_sha=context.active_commit_sha or "",
                model_fingerprint=embedding_model_fingerprint(
                    self._settings, self._preprocessor.policy_fingerprint
                ),
                preprocessing_fingerprint=self._preprocessor.policy_fingerprint,
            )
            if len(reused) != len(reusable):
                reused = {}
                await self._store.record_freshness_plan(
                    claimed,
                    self._worker_id,
                    actual_mode=IndexingMode.FULL,
                    fallback_reason="previous_artifact_missing",
                    changed_counts=plan.changes.counts,
                    changed_file_count=len(plan.changes.target_paths | plan.changes.removed_paths),
                )
        to_embed = tuple(document for document in prepared if document.item_id not in reused)
        embeddings = await self._embeddings.embed_documents(to_embed)
        if len(embeddings) != len(to_embed):
            raise IndexingError(
                code="embedding_result_count_mismatch",
                message="The embedding service returned an invalid response",
                retryable=False,
            )
        await self._store.stage(
            claimed,
            self._worker_id,
            status=RepositoryIndexingStatus.FINALIZING,
            stage="storing_vectors",
            progress=92,
            commit_sha=cloned.commit_sha,
        )
        scope = self._scope(context)
        if not await self._store.can_cleanup_inactive(context.repository_id, context.index_version):
            raise IndexingError(
                code="index_activation_race",
                message="The inactive index is no longer eligible for activation",
                retryable=False,
            )
        await self._vectors.ensure_collection()
        await self._vectors.delete_scope(scope)
        records = tuple(
            build_vector_record(
                scope=scope,
                prepared=document,
                vector=reused.get(document.item_id, embeddings.get(document.item_id, ())),
                settings=self._settings,
                policy_fingerprint=self._preprocessor.policy_fingerprint,
            )
            for document in prepared
        )
        await self._vectors.upsert(scope, records)
        await self._store.record_vector_counts(
            claimed,
            self._worker_id,
            embedded_chunk_count=len(prepared),
            vector_count=len(records),
            reused_chunk_count=len(reused),
            reembedded_chunk_count=len(to_embed),
        )
        return len(records)

    async def _validate_and_activate(
        self,
        claimed: ClaimedJob,
        context: JobContext,
        cloned: ClonedRepository,
        vector_count: int,
    ) -> bool:
        await self._store.stage(
            claimed,
            self._worker_id,
            status=RepositoryIndexingStatus.FINALIZING,
            stage="validating_index",
            progress=96,
            commit_sha=cloned.commit_sha,
        )
        await self._vectors.validate_scope(
            self._scope(context),
            expected_count=vector_count,
            commit_sha=cloned.commit_sha,
            model_fingerprint=embedding_model_fingerprint(
                self._settings, self._preprocessor.policy_fingerprint
            ),
        )
        await self._store.validate_graph(claimed, self._worker_id)
        await self._store.mark_build_ready(claimed, self._worker_id)
        if not await self._store.is_current(claimed, self._worker_id):
            await self._cleanup_inactive(context)
            await self._store.mark_stale(
                claimed,
                self._worker_id,
                code="refresh_superseded_before_activation",
                superseded=True,
            )
            return False
        reauthorized = await self._store.authorized_context(claimed)
        if reauthorized is None:
            await self._cleanup_inactive(context)
            await self._store.cancel_revoked(claimed, self._worker_id)
            return False
        if reauthorized != context:
            raise IndexingError(
                code="index_activation_race",
                message="The inactive index is no longer eligible for activation",
                retryable=False,
            )
        await self._store.stage(
            claimed,
            self._worker_id,
            status=RepositoryIndexingStatus.FINALIZING,
            stage="activating_index",
            progress=99,
            commit_sha=cloned.commit_sha,
        )
        previous_version = await self._store.activate(
            claimed, self._worker_id, commit_sha=cloned.commit_sha
        )
        if previous_version > 0:
            await self._cleanup_superseded(context, previous_version)
        return True

    @staticmethod
    def _log_completion(
        claimed: ClaimedJob,
        context: JobContext,
        discovery: DiscoveryResult,
        processing: ProcessingResult,
        vector_count: int,
    ) -> None:
        logger.info(
            "indexing_job_completed",
            job_id=str(claimed.id),
            repository_id=str(claimed.repository_id),
            discovered_file_count=len(discovery.files),
            skipped_file_count=sum(discovery.skipped.values()),
            parsed_file_count=processing.parsed_file_count,
            partial_file_count=processing.partial_file_count,
            parser_skipped_file_count=processing.skipped_file_count,
            symbol_count=processing.symbol_count,
            chunk_count=processing.chunk_count,
            vector_count=vector_count,
            call_site_count=processing.call_site_count,
            exact_edge_count=processing.exact_edge_count,
            ambiguous_edge_count=processing.ambiguous_edge_count,
            unresolved_call_count=processing.unresolved_call_count,
            graph_warning_count=processing.graph_warning_count,
            index_version=context.index_version,
        )

    def _scope(self, context: JobContext, index_version: int | None = None) -> VectorScope:
        return VectorScope(
            installation_id=context.installation_id,
            repository_id=context.repository_id,
            index_version=context.index_version if index_version is None else index_version,
        )

    async def _cleanup_inactive(self, context: JobContext) -> bool:
        if not await self._store.can_cleanup_inactive(context.repository_id, context.index_version):
            return False
        try:
            await self._vectors.delete_scope(self._scope(context))
        except IndexingError as error:
            await self._store.record_failed_build_cleanup(
                context.repository_id,
                context.index_version,
                cleanup_complete=False,
            )
            logger.warning(
                "inactive_index_cleanup_failed",
                repository_id=str(context.repository_id),
                index_version=context.index_version,
                error_code=error.code,
            )
            return False
        except Exception as error:  # noqa: BLE001 -- keep internals out of logs
            await self._store.record_failed_build_cleanup(
                context.repository_id,
                context.index_version,
                cleanup_complete=False,
            )
            logger.error(  # noqa: TRY400 -- tracebacks may contain private clone paths
                "inactive_index_cleanup_internal_error",
                repository_id=str(context.repository_id),
                index_version=context.index_version,
                error_type=type(error).__name__,
            )
            return False
        await self._store.record_failed_build_cleanup(
            context.repository_id,
            context.index_version,
            cleanup_complete=True,
        )
        return True

    async def _cleanup_superseded(self, context: JobContext, index_version: int) -> None:
        try:
            await self._vectors.delete_scope(self._scope(context, index_version))
            await self._store.complete_superseded_cleanup(context.repository_id, index_version)
        except IndexingError as error:
            logger.warning(
                "superseded_index_cleanup_pending",
                repository_id=str(context.repository_id),
                index_version=index_version,
                error_code=error.code,
            )
        except Exception as error:  # noqa: BLE001 -- activation is already committed
            logger.error(  # noqa: TRY400 -- tracebacks may contain private clone paths
                "superseded_index_cleanup_internal_error",
                repository_id=str(context.repository_id),
                index_version=index_version,
                error_type=type(error).__name__,
            )

    async def _heartbeat(self, claimed: ClaimedJob, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._settings.worker_heartbeat_interval_seconds,
                )
            except TimeoutError:
                await self._store.heartbeat(claimed, self._worker_id)
