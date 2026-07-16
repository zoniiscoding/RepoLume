"""Atomic PostgreSQL state transitions for Milestone 3 workers."""

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select, update

from app.core.config import Settings
from app.db.models.enums import (
    IndexingJobStatus,
    InstallationStatus,
    RepositoryIndexingStatus,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.models.symbol_definition import SymbolDefinition
from app.db.session import Database
from app.indexing.discovery import DiscoveryResult
from app.indexing.models import ProcessingResult


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: uuid.UUID
    repository_id: uuid.UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class JobContext:
    job_id: uuid.UUID
    repository_id: uuid.UUID
    github_installation_id: int
    owner: str
    name: str
    default_branch: str
    index_version: int


class IndexingJobStore:
    """Keep transitions conditional so duplicate workers cannot share one job."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._membership_ttl = timedelta(seconds=settings.installation_membership_ttl_seconds)
        self._abandoned_after = timedelta(seconds=settings.worker_abandoned_after_seconds)
        self._max_attempts = settings.worker_max_attempts
        self._retry_base = settings.worker_retry_base_seconds
        self._retry_max = settings.worker_retry_max_seconds

    async def claim(self, job_id: uuid.UUID, worker_id: str) -> ClaimedJob | None:
        now = datetime.now(UTC)
        async with self._database.session() as session:
            result = await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == job_id,
                    IndexingJob.status.in_((IndexingJobStatus.QUEUED, IndexingJobStatus.RETRYING)),
                    or_(
                        IndexingJob.next_attempt_at.is_(None),
                        IndexingJob.next_attempt_at <= now,
                    ),
                )
                .values(
                    status=IndexingJobStatus.RUNNING,
                    attempt=IndexingJob.attempt + 1,
                    locked_by=worker_id,
                    started_at=now,
                    heartbeat_at=now,
                    next_attempt_at=None,
                    error_code=None,
                    safe_error_message=None,
                )
                .returning(IndexingJob.id, IndexingJob.repository_id, IndexingJob.attempt)
            )
            row = result.one_or_none()
            if row is None:
                await session.rollback()
                return None
            await session.execute(
                update(Repository)
                .where(Repository.id == row.repository_id)
                .values(
                    indexing_status=RepositoryIndexingStatus.CLONING,
                    indexing_progress=5,
                    indexing_stage="cloning",
                    indexing_error_code=None,
                    indexing_error_message=None,
                )
            )
            await session.commit()
            return ClaimedJob(id=row.id, repository_id=row.repository_id, attempt=row.attempt)

    async def authorized_context(self, claimed: ClaimedJob) -> JobContext | None:
        cutoff = datetime.now(UTC) - self._membership_ttl
        async with self._database.session() as session:
            row = (
                await session.execute(
                    select(IndexingJob, Repository, GitHubInstallation)
                    .join(Repository, Repository.id == IndexingJob.repository_id)
                    .join(
                        GitHubInstallation,
                        GitHubInstallation.id == Repository.installation_id,
                    )
                    .join(
                        InstallationMember,
                        and_(
                            InstallationMember.installation_id == GitHubInstallation.id,
                            InstallationMember.user_id == IndexingJob.requested_by_user_id,
                        ),
                    )
                    .where(
                        IndexingJob.id == claimed.id,
                        IndexingJob.status == IndexingJobStatus.RUNNING,
                        Repository.access_revoked_at.is_(None),
                        Repository.deleted_at.is_(None),
                        GitHubInstallation.status == InstallationStatus.ACTIVE,
                        GitHubInstallation.deleted_at.is_(None),
                        InstallationMember.verified_at >= cutoff,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        _, repository, installation = row
        return JobContext(
            job_id=claimed.id,
            repository_id=repository.id,
            github_installation_id=installation.github_installation_id,
            owner=repository.github_owner,
            name=repository.github_name,
            default_branch=repository.default_branch,
            index_version=repository.index_version + 1,
        )

    async def heartbeat(self, claimed: ClaimedJob, worker_id: str) -> None:
        async with self._database.session() as session:
            await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == claimed.id,
                    IndexingJob.status == IndexingJobStatus.RUNNING,
                    IndexingJob.locked_by == worker_id,
                )
                .values(heartbeat_at=datetime.now(UTC))
            )
            await session.commit()

    async def stage(
        self,
        claimed: ClaimedJob,
        worker_id: str,
        *,
        status: RepositoryIndexingStatus,
        stage: str,
        progress: int,
        commit_sha: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        async with self._database.session() as session:
            result = await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == claimed.id,
                    IndexingJob.status == IndexingJobStatus.RUNNING,
                    IndexingJob.locked_by == worker_id,
                )
                .values(
                    stage=stage,
                    progress=progress,
                    heartbeat_at=now,
                    source_commit_sha=commit_sha
                    if commit_sha is not None
                    else IndexingJob.source_commit_sha,
                )
                .returning(IndexingJob.repository_id)
            )
            repository_id = result.scalar_one_or_none()
            if repository_id is not None:
                values: dict[str, object] = {
                    "indexing_status": status,
                    "indexing_stage": stage,
                    "indexing_progress": progress,
                }
                if commit_sha is not None:
                    values["current_remote_sha"] = commit_sha
                await session.execute(
                    update(Repository).where(Repository.id == repository_id).values(**values)
                )
            await session.commit()

    async def complete(
        self,
        claimed: ClaimedJob,
        worker_id: str,
        *,
        commit_sha: str,
        discovery: DiscoveryResult,
        processing: ProcessingResult,
    ) -> None:
        if (
            processing.repository_id != claimed.repository_id
            or processing.commit_sha != commit_sha
            or processing.index_version < 1
        ):
            raise RuntimeError("processing_result_mismatch")
        now = datetime.now(UTC)
        async with self._database.session() as session:
            result = await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == claimed.id,
                    IndexingJob.status == IndexingJobStatus.RUNNING,
                    IndexingJob.locked_by == worker_id,
                )
                .values(
                    status=IndexingJobStatus.COMPLETE,
                    progress=100,
                    stage="chunking_complete",
                    source_commit_sha=commit_sha,
                    target_commit_sha=commit_sha,
                    heartbeat_at=now,
                    completed_at=now,
                    locked_by=None,
                    discovered_file_count=len(discovery.files),
                    discovered_total_bytes=discovery.total_bytes,
                    skipped_files_json=discovery.skipped,
                    parsed_file_count=processing.parsed_file_count,
                    partial_file_count=processing.partial_file_count,
                    parser_skipped_file_count=processing.skipped_file_count,
                    symbol_count=processing.symbol_count,
                    chunk_count=processing.chunk_count,
                    parser_warnings_json=processing.warning_counts,
                )
                .returning(IndexingJob.repository_id)
            )
            repository_id = result.scalar_one_or_none()
            if repository_id is not None:
                await session.execute(
                    delete(SymbolDefinition).where(
                        SymbolDefinition.repository_id == repository_id,
                        SymbolDefinition.index_version == processing.index_version,
                    )
                )
                session.add_all(
                    SymbolDefinition(
                        repository_id=repository_id,
                        index_version=processing.index_version,
                        file_path=symbol.file_path,
                        language=symbol.language,
                        symbol_name=symbol.symbol_name,
                        qualified_name=symbol.qualified_name,
                        symbol_type=symbol.symbol_type,
                        start_line=symbol.start_line,
                        end_line=symbol.end_line,
                        content_hash=symbol.content_hash,
                        commit_sha=symbol.commit_sha,
                    )
                    for symbol in processing.symbols
                )
                await session.execute(
                    update(Repository)
                    .where(Repository.id == repository_id)
                    .values(
                        indexing_status=RepositoryIndexingStatus.NOT_INDEXED,
                        indexing_progress=100,
                        indexing_stage="chunking_complete",
                        indexing_error_code=None,
                        indexing_error_message=None,
                        current_remote_sha=commit_sha,
                        size_bytes=discovery.total_bytes,
                    )
                )
            await session.commit()

    async def cancel_revoked(self, claimed: ClaimedJob, worker_id: str) -> None:
        now = datetime.now(UTC)
        async with self._database.session() as session:
            result = await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == claimed.id,
                    IndexingJob.status == IndexingJobStatus.RUNNING,
                    IndexingJob.locked_by == worker_id,
                )
                .values(
                    status=IndexingJobStatus.CANCELLED,
                    error_code="repository_access_revoked",
                    safe_error_message="Repository access is no longer authorized",
                    completed_at=now,
                    heartbeat_at=now,
                    locked_by=None,
                )
                .returning(IndexingJob.repository_id)
            )
            repository_id = result.scalar_one_or_none()
            if repository_id is not None:
                await session.execute(
                    update(Repository)
                    .where(Repository.id == repository_id)
                    .values(
                        indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
                        indexing_error_code="repository_access_revoked",
                        indexing_error_message="Repository access is no longer authorized",
                    )
                )
            await session.commit()

    async def fail(
        self,
        claimed: ClaimedJob,
        worker_id: str,
        *,
        code: str,
        safe_message: str,
        retryable: bool,
    ) -> bool:
        now = datetime.now(UTC)
        should_retry = retryable and claimed.attempt < self._max_attempts
        status = IndexingJobStatus.RETRYING if should_retry else IndexingJobStatus.FAILED
        next_attempt = now + self._retry_delay(claimed.attempt) if should_retry else None
        async with self._database.session() as session:
            result = await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == claimed.id,
                    IndexingJob.status == IndexingJobStatus.RUNNING,
                    IndexingJob.locked_by == worker_id,
                )
                .values(
                    status=status,
                    error_code=code,
                    safe_error_message=safe_message,
                    next_attempt_at=next_attempt,
                    completed_at=None if should_retry else now,
                    heartbeat_at=now,
                    locked_by=None,
                    stage="retry_wait" if should_retry else "failed",
                )
                .returning(IndexingJob.repository_id)
            )
            repository_id = result.scalar_one_or_none()
            if repository_id is not None:
                await session.execute(
                    update(Repository)
                    .where(Repository.id == repository_id)
                    .values(
                        indexing_status=(
                            RepositoryIndexingStatus.QUEUED
                            if should_retry
                            else RepositoryIndexingStatus.FAILED
                        ),
                        indexing_stage="retry_wait" if should_retry else "failed",
                        indexing_error_code=code,
                        indexing_error_message=safe_message,
                    )
                )
            await session.commit()
        return should_retry

    async def recover_abandoned(self) -> int:
        cutoff = datetime.now(UTC) - self._abandoned_after
        async with self._database.session() as session:
            jobs = tuple(
                (
                    await session.scalars(
                        select(IndexingJob).where(
                            IndexingJob.status == IndexingJobStatus.RUNNING,
                            or_(
                                IndexingJob.heartbeat_at.is_(None),
                                IndexingJob.heartbeat_at < cutoff,
                            ),
                        )
                    )
                ).all()
            )
            for job in jobs:
                retry = job.attempt < self._max_attempts
                job.status = IndexingJobStatus.RETRYING if retry else IndexingJobStatus.FAILED
                job.next_attempt_at = datetime.now(UTC) if retry else None
                job.locked_by = None
                job.error_code = "worker_abandoned"
                job.safe_error_message = "Worker stopped before the job completed"
                job.stage = "retry_wait" if retry else "failed"
                if not retry:
                    job.completed_at = datetime.now(UTC)
                await session.execute(
                    update(Repository)
                    .where(Repository.id == job.repository_id)
                    .values(
                        indexing_status=(
                            RepositoryIndexingStatus.QUEUED
                            if retry
                            else RepositoryIndexingStatus.FAILED
                        ),
                        indexing_stage=job.stage,
                        indexing_error_code=job.error_code,
                        indexing_error_message=job.safe_error_message,
                    )
                )
            await session.commit()
            return len(jobs)

    async def due_jobs(self) -> Sequence[uuid.UUID]:
        now = datetime.now(UTC)
        duplicate_cutoff = now - self._abandoned_after
        async with self._database.session() as session:
            result = await session.scalars(
                select(IndexingJob.id)
                .where(
                    IndexingJob.status.in_((IndexingJobStatus.QUEUED, IndexingJobStatus.RETRYING)),
                    or_(
                        IndexingJob.next_attempt_at.is_(None),
                        IndexingJob.next_attempt_at <= now,
                    ),
                    or_(
                        IndexingJob.last_enqueued_at.is_(None),
                        IndexingJob.last_enqueued_at < duplicate_cutoff,
                    ),
                )
                .order_by(IndexingJob.created_at)
                .limit(100)
            )
            return tuple(result.all())

    async def mark_enqueued(self, job_id: uuid.UUID) -> None:
        async with self._database.session() as session:
            await session.execute(
                update(IndexingJob)
                .where(
                    IndexingJob.id == job_id,
                    IndexingJob.status.in_((IndexingJobStatus.QUEUED, IndexingJobStatus.RETRYING)),
                )
                .values(last_enqueued_at=datetime.now(UTC))
            )
            await session.commit()

    def _retry_delay(self, attempt: int) -> timedelta:
        base = min(self._retry_max, self._retry_base * (2 ** max(0, attempt - 1)))
        jitter_milliseconds = secrets.randbelow(base * 500 + 1)
        return timedelta(seconds=base, milliseconds=jitter_milliseconds)
