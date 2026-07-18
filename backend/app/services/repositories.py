"""Tenant-safe repository selection and durable indexing job creation."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import Settings
from app.db.models.enums import (
    IndexingJobStatus,
    IndexingJobType,
    IndexingMode,
    InstallationStatus,
    RepositoryIndexingStatus,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.session import Database
from app.queue import JobQueueProtocol
from app.services.installations import InstallationAccessError, InstallationService


class RepositoryAccessError(LookupError):
    """Hide whether a repository exists in another tenant."""


@dataclass(frozen=True, slots=True)
class RepositoryJob:
    repository: Repository
    job: IndexingJob


class RepositoryService:
    """Persist work before sending an opaque wakeup to Redis."""

    def __init__(
        self,
        *,
        database: Database,
        queue: JobQueueProtocol,
        installations: InstallationService,
        settings: Settings,
    ) -> None:
        self._database = database
        self._queue = queue
        self._installations = installations
        self._membership_ttl = timedelta(seconds=settings.installation_membership_ttl_seconds)

    async def list_authorized(self, user_id: uuid.UUID) -> Sequence[Repository]:
        return await self._installations.list_authorized_repositories(user_id)

    async def select_repository(
        self,
        *,
        user_id: uuid.UUID,
        installation_id: uuid.UUID,
        github_repository_id: int,
    ) -> RepositoryJob:
        repository = await self._installations.synchronize_repository(
            user_id=user_id,
            installation_id=installation_id,
            github_repository_id=github_repository_id,
        )

        async with self._database.session() as session:
            membership_cutoff = datetime.now(UTC) - self._membership_ttl
            locked_repository = await session.scalar(
                select(Repository)
                .join(
                    GitHubInstallation,
                    GitHubInstallation.id == Repository.installation_id,
                )
                .join(
                    InstallationMember,
                    InstallationMember.installation_id == GitHubInstallation.id,
                )
                .where(
                    Repository.id == repository.id,
                    Repository.installation_id == installation_id,
                    Repository.access_revoked_at.is_(None),
                    Repository.deleted_at.is_(None),
                    GitHubInstallation.status == InstallationStatus.ACTIVE,
                    GitHubInstallation.deleted_at.is_(None),
                    InstallationMember.user_id == user_id,
                    InstallationMember.verified_at >= membership_cutoff,
                )
                .with_for_update()
            )
            if locked_repository is None:
                raise RepositoryAccessError

            job = await session.scalar(
                select(IndexingJob)
                .where(
                    IndexingJob.repository_id == locked_repository.id,
                    IndexingJob.job_type == IndexingJobType.INITIAL_INDEX,
                )
                .order_by(IndexingJob.created_at.desc())
                .limit(1)
            )
            if job is None:
                job = IndexingJob(
                    repository_id=locked_repository.id,
                    requested_by_user_id=user_id,
                    job_type=IndexingJobType.INITIAL_INDEX,
                    status=IndexingJobStatus.QUEUED,
                    stage="queued",
                    target_branch=locked_repository.default_branch,
                    refresh_generation=locked_repository.refresh_generation,
                )
                session.add(job)
                locked_repository.indexing_status = RepositoryIndexingStatus.QUEUED
                locked_repository.indexing_progress = 0
                locked_repository.indexing_stage = "queued"
                locked_repository.indexing_error_code = None
                locked_repository.indexing_error_message = None
                await session.flush()
            await session.commit()

        await self._queue.enqueue(job.id)
        async with self._database.session() as session:
            await session.execute(
                update(IndexingJob)
                .where(IndexingJob.id == job.id)
                .values(last_enqueued_at=datetime.now(UTC))
            )
            await session.commit()
        return RepositoryJob(repository=locked_repository, job=job)

    async def get_authorized(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
    ) -> Repository:
        try:
            return await self._installations.get_authorized_repository(
                user_id=user_id,
                repository_id=repository_id,
            )
        except InstallationAccessError as error:
            raise RepositoryAccessError from error

    async def reindex(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
    ) -> RepositoryJob:
        """Create a server-scoped full refresh that supersedes older generations."""
        await self.get_authorized(user_id=user_id, repository_id=repository_id)
        async with self._database.session() as session:
            repository = await session.get(Repository, repository_id, with_for_update=True)
            if repository is None:
                raise RepositoryAccessError
            repository.refresh_generation += 1
            job = IndexingJob(
                repository_id=repository.id,
                requested_by_user_id=user_id,
                job_type=IndexingJobType.MANUAL_REINDEX,
                status=IndexingJobStatus.QUEUED,
                stage="queued",
                target_commit_sha=repository.current_remote_sha,
                target_branch=repository.default_branch,
                requested_mode=IndexingMode.FULL,
                full_rebuild_reason="manual_reindex",
                refresh_generation=repository.refresh_generation,
            )
            session.add(job)
            repository.indexing_status = RepositoryIndexingStatus.QUEUED
            repository.indexing_progress = 0
            repository.indexing_stage = "queued_full_rebuild"
            await session.flush()
            await session.commit()
        await self._queue.enqueue(job.id)
        async with self._database.session() as session:
            await session.execute(
                update(IndexingJob)
                .where(IndexingJob.id == job.id)
                .values(last_enqueued_at=datetime.now(UTC))
            )
            await session.commit()
        return RepositoryJob(repository=repository, job=job)

    async def get_status(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
    ) -> RepositoryJob | tuple[Repository, None]:
        repository = await self.get_authorized(user_id=user_id, repository_id=repository_id)
        async with self._database.session() as session:
            job = await session.scalar(
                select(IndexingJob)
                .where(IndexingJob.repository_id == repository.id)
                .order_by(IndexingJob.created_at.desc())
                .limit(1)
            )
        if job is None:
            return repository, None
        return RepositoryJob(repository=repository, job=job)
