"""Authenticated public GitHub import, membership, reuse, and refresh service."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update

from app.core.config import Settings
from app.db.models.enums import (
    IndexingJobStatus,
    IndexingJobType,
    IndexingMode,
    RepositoryAccessMode,
    RepositoryIndexingStatus,
)
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.models.repository_index_build import RepositoryIndexBuild
from app.db.models.user_repository import UserRepository
from app.db.session import Database
from app.github.client import GitHubClientProtocol, PublicGitHubRepository
from app.github.public_urls import PublicRepositoryURL
from app.queue import JobQueueProtocol


class PublicRepositoryLimitError(ValueError):
    """A per-user public import or attachment bound was reached."""


class PublicRepositoryTooLargeError(ValueError):
    """Provider metadata exceeds the configured clone ceiling."""


class PublicRepositoryAccessError(LookupError):
    """The user is not attached to the requested public repository."""


@dataclass(frozen=True, slots=True)
class PublicRepositoryResult:
    repository: Repository
    job: IndexingJob | None
    already_current: bool
    reused_index: bool


class PublicRepositoryService:
    """Use trusted GitHub identity, shared repository rows, and per-user memberships."""

    def __init__(
        self,
        *,
        database: Database,
        github: GitHubClientProtocol,
        queue: JobQueueProtocol,
        settings: Settings,
    ) -> None:
        self._database = database
        self._github = github
        self._queue = queue
        self._repository_limit = settings.public_repository_limit_per_user
        self._active_limit = settings.public_import_active_limit_per_user
        self._max_repository_bytes = settings.clone_max_repository_bytes

    async def import_repository(
        self, *, user_id: uuid.UUID, parsed_url: PublicRepositoryURL
    ) -> PublicRepositoryResult:
        metadata = await self._github.get_public_repository(
            owner=parsed_url.owner, repository=parsed_url.repository
        )
        self._validate_size(metadata)
        result, enqueue_id = await self._attach(user_id=user_id, metadata=metadata)
        if enqueue_id is not None:
            await self._enqueue(enqueue_id)
        return result

    async def refresh(
        self, *, user_id: uuid.UUID, repository_id: uuid.UUID
    ) -> PublicRepositoryResult:
        async with self._database.session() as session:
            repository = await session.scalar(
                select(Repository)
                .join(UserRepository, UserRepository.repository_id == Repository.id)
                .where(
                    Repository.id == repository_id,
                    Repository.access_mode == RepositoryAccessMode.PUBLIC,
                    Repository.deleted_at.is_(None),
                    UserRepository.user_id == user_id,
                )
            )
        if repository is None:
            raise PublicRepositoryAccessError
        metadata = await self._github.get_public_repository(
            owner=repository.github_owner, repository=repository.github_name
        )
        if metadata.repository.id != repository.github_repository_id:
            await self._revoke(repository.id)
            raise PublicRepositoryAccessError
        self._validate_size(metadata)
        result, enqueue_id = await self._attach(
            user_id=user_id,
            metadata=metadata,
            expected_repository_id=repository.id,
            manual=True,
        )
        if enqueue_id is not None:
            await self._enqueue(enqueue_id)
        return result

    async def revoke(self, repository_id: uuid.UUID) -> None:
        await self._revoke(repository_id)

    def _validate_size(self, metadata: PublicGitHubRepository) -> None:
        size_kib = metadata.repository.size
        if size_kib is not None and size_kib * 1024 > self._max_repository_bytes:
            raise PublicRepositoryTooLargeError

    async def _attach(
        self,
        *,
        user_id: uuid.UUID,
        metadata: PublicGitHubRepository,
        expected_repository_id: uuid.UUID | None = None,
        manual: bool = False,
    ) -> tuple[PublicRepositoryResult, uuid.UUID | None]:
        external = metadata.repository
        now = datetime.now(UTC)
        async with self._database.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:repository_id)").bindparams(
                    repository_id=external.id
                )
            )
            repository = await session.scalar(
                select(Repository)
                .where(
                    Repository.access_mode == RepositoryAccessMode.PUBLIC,
                    Repository.github_repository_id == external.id,
                )
                .with_for_update()
            )
            if expected_repository_id is not None and (
                repository is None or repository.id != expected_repository_id
            ):
                raise PublicRepositoryAccessError
            membership = None
            if repository is not None:
                membership = await session.scalar(
                    select(UserRepository).where(
                        UserRepository.user_id == user_id,
                        UserRepository.repository_id == repository.id,
                    )
                )
            if membership is None:
                attachment_count = await session.scalar(
                    select(func.count())
                    .select_from(UserRepository)
                    .where(UserRepository.user_id == user_id)
                )
                if (attachment_count or 0) >= self._repository_limit:
                    raise PublicRepositoryLimitError
                active_count = await session.scalar(
                    select(func.count())
                    .select_from(IndexingJob)
                    .where(
                        IndexingJob.requested_by_user_id == user_id,
                        IndexingJob.status.in_(
                            (
                                IndexingJobStatus.QUEUED,
                                IndexingJobStatus.RUNNING,
                                IndexingJobStatus.RETRYING,
                            )
                        ),
                    )
                )
                if (active_count or 0) >= self._active_limit:
                    raise PublicRepositoryLimitError
            if repository is None:
                repository = Repository(
                    installation_id=None,
                    access_mode=RepositoryAccessMode.PUBLIC,
                    github_repository_id=external.id,
                    github_owner=external.owner.login,
                    github_name=external.name,
                    github_full_name=external.full_name,
                    github_url=external.html_url,
                    is_private=False,
                    default_branch=external.default_branch,
                    current_remote_sha=metadata.default_branch_sha,
                    primary_language=external.language,
                    size_bytes=None if external.size is None else external.size * 1024,
                    visibility_checked_at=now,
                )
                session.add(repository)
                await session.flush()
            repository.github_owner = external.owner.login
            repository.github_name = external.name
            repository.github_full_name = external.full_name
            repository.github_url = external.html_url
            repository.is_private = False
            repository.default_branch = external.default_branch
            repository.current_remote_sha = metadata.default_branch_sha
            repository.primary_language = external.language
            repository.size_bytes = None if external.size is None else external.size * 1024
            repository.visibility_checked_at = now
            repository.access_revoked_at = None
            repository.deleted_at = None
            if membership is None:
                session.add(UserRepository(user_id=user_id, repository_id=repository.id))

            active_build = await session.scalar(
                select(RepositoryIndexBuild).where(
                    RepositoryIndexBuild.repository_id == repository.id,
                    RepositoryIndexBuild.index_version == repository.index_version,
                    RepositoryIndexBuild.state == "active",
                )
            )
            already_current = (
                active_build is not None
                and active_build.commit_sha == metadata.default_branch_sha
                and repository.last_indexed_commit_sha == metadata.default_branch_sha
            )
            job = await session.scalar(
                select(IndexingJob)
                .where(
                    IndexingJob.repository_id == repository.id,
                    IndexingJob.status.in_(
                        (
                            IndexingJobStatus.QUEUED,
                            IndexingJobStatus.RUNNING,
                            IndexingJobStatus.RETRYING,
                        )
                    ),
                )
                .order_by(IndexingJob.created_at.desc())
                .limit(1)
            )
            enqueue_id: uuid.UUID | None = None
            if not already_current and job is None:
                repository.refresh_generation += 1 if manual else 0
                job = IndexingJob(
                    repository_id=repository.id,
                    requested_by_user_id=user_id,
                    job_type=(
                        IndexingJobType.MANUAL_REINDEX if manual else IndexingJobType.INITIAL_INDEX
                    ),
                    status=IndexingJobStatus.QUEUED,
                    stage="queued",
                    target_commit_sha=metadata.default_branch_sha,
                    target_branch=external.default_branch,
                    requested_mode=IndexingMode.INCREMENTAL if manual else IndexingMode.FULL,
                    refresh_generation=repository.refresh_generation,
                )
                session.add(job)
                repository.indexing_status = RepositoryIndexingStatus.QUEUED
                repository.indexing_progress = 0
                repository.indexing_stage = "queued"
                await session.flush()
                enqueue_id = job.id
            await session.commit()
            return (
                PublicRepositoryResult(
                    repository=repository,
                    job=job,
                    already_current=already_current,
                    reused_index=active_build is not None,
                ),
                enqueue_id,
            )

    async def _enqueue(self, job_id: uuid.UUID) -> None:
        await self._queue.enqueue(job_id)
        async with self._database.session() as session:
            await session.execute(
                update(IndexingJob)
                .where(IndexingJob.id == job_id)
                .values(last_enqueued_at=datetime.now(UTC))
            )
            await session.commit()

    async def _revoke(self, repository_id: uuid.UUID) -> None:
        async with self._database.session() as session:
            await session.execute(
                update(Repository)
                .where(
                    Repository.id == repository_id,
                    Repository.access_mode == RepositoryAccessMode.PUBLIC,
                )
                .values(
                    access_revoked_at=datetime.now(UTC),
                    indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
                    indexing_stage="public_access_unavailable",
                )
            )
            await session.commit()
