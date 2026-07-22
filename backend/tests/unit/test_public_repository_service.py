"""Public repository service behavior without external GitHub or database access."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest

from app.db.models.enums import IndexingJobStatus, RepositoryAccessMode
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.github.client import GitHubClientProtocol, PublicGitHubRepository
from app.github.public_urls import PublicRepositoryURL
from app.github.schemas import GitHubRepository
from app.queue import JobQueueProtocol
from app.services.public_repositories import (
    PublicRepositoryService,
    PublicRepositoryTooLargeError,
)
from tests.conftest import make_settings


def metadata(*, size: int | None = 10) -> PublicGitHubRepository:
    return PublicGitHubRepository(
        repository=GitHubRepository.model_validate(
            {
                "id": 9001,
                "owner": {"login": "owner"},
                "name": "repo",
                "full_name": "owner/repo",
                "html_url": "https://github.com/owner/repo",
                "private": False,
                "default_branch": "main",
                "language": "Python",
                "size": size,
            }
        ),
        default_branch_sha="a" * 40,
    )


class FakeSession:
    def __init__(self, scalar_results: list[object | None]) -> None:
        self.scalar_results = scalar_results
        self.added: list[object] = []
        self.execute_count = 0
        self.commit_count = 0

    async def execute(self, statement: object) -> None:
        del statement
        self.execute_count += 1

    async def scalar(self, statement: object) -> object | None:
        del statement
        return self.scalar_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            item = cast(Any, value)
            if hasattr(item, "id") and item.id is None:
                item.id = uuid.uuid4()
            if isinstance(value, Repository):
                value.refresh_generation = value.refresh_generation or 0
                value.index_version = value.index_version or 0

    async def commit(self) -> None:
        self.commit_count += 1


class FakeDatabase:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    @asynccontextmanager
    async def session(self) -> AsyncIterator[FakeSession]:
        yield self._session


class FakeGitHub:
    def __init__(self, result: PublicGitHubRepository) -> None:
        self.result = result
        self.requests: list[tuple[str, str]] = []

    async def get_public_repository(self, *, owner: str, repository: str) -> PublicGitHubRepository:
        self.requests.append((owner, repository))
        return self.result


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue(self, job_id: uuid.UUID) -> None:
        self.enqueued.append(job_id)


@pytest.mark.asyncio
async def test_import_creates_shared_public_repository_membership_and_durable_job() -> None:
    session = FakeSession([None, 0, 0, None, None])
    database = FakeDatabase(session)
    github = FakeGitHub(metadata())
    queue = FakeQueue()
    service = PublicRepositoryService(
        database=cast(Any, database),
        github=cast(GitHubClientProtocol, github),
        queue=cast(JobQueueProtocol, queue),
        settings=make_settings(),
    )
    user_id = uuid.uuid4()

    result = await service.import_repository(
        user_id=user_id,
        parsed_url=PublicRepositoryURL(owner="owner", repository="repo"),
    )

    assert github.requests == [("owner", "repo")]
    assert result.repository.access_mode is RepositoryAccessMode.PUBLIC
    assert result.repository.installation_id is None
    assert result.repository.github_repository_id == 9001
    assert result.repository.size_bytes == 10 * 1024
    assert result.job is not None
    assert result.job.status is IndexingJobStatus.QUEUED
    assert queue.enqueued == [result.job.id]
    assert any(isinstance(item, Repository) for item in session.added)
    assert any(isinstance(item, IndexingJob) for item in session.added)
    assert session.execute_count == 2
    assert session.commit_count == 2


def test_import_preflight_rejects_repository_larger_than_clone_limit() -> None:
    service = PublicRepositoryService(
        database=cast(Any, object()),
        github=cast(GitHubClientProtocol, object()),
        queue=cast(JobQueueProtocol, object()),
        settings=make_settings(clone_max_repository_bytes=1024),
    )

    with pytest.raises(PublicRepositoryTooLargeError):
        service._validate_size(metadata(size=2))

    service._validate_size(metadata(size=None))
