"""PostgreSQL, Redis, API, worker, and controlled Git fixture integration tests."""

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.application import create_app
from app.auth.tokens import TokenService
from app.core.config import Settings
from app.db.models.enums import (
    GitHubAccountType,
    IndexingJobStatus,
    IndexingJobType,
    InstallationMemberRole,
    InstallationStatus,
    RepositorySelection,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.indexing_job import IndexingJob
from app.db.models.user import User
from app.db.session import Database
from app.github.schemas import GitHubInstallation as GitHubInstallationData
from app.github.schemas import GitHubRepository, GitHubUser
from app.indexing.clone import ClonedRepository, CloneRequest
from app.indexing.discovery import FileDiscovery
from app.indexing.failures import IndexingError
from app.indexing.worker import IndexingWorker
from app.queue import RedisJobQueue
from app.services.indexing_jobs import IndexingJobStore
from tests.conftest import make_settings

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.fail("TEST_DATABASE_URL must target a disposable PostgreSQL database")
    return value


def redis_url() -> str:
    value = os.environ.get("TEST_REDIS_URL")
    if value is None:
        pytest.fail("TEST_REDIS_URL must target a disposable Redis database")
    return value


def integration_settings(**overrides: object) -> Settings:
    return make_settings(
        database_url=database_url(),
        redis_url=redis_url(),
        worker_poll_timeout_ms=100,
        worker_abandoned_after_seconds=5,
        worker_retry_base_seconds=1,
        worker_retry_max_seconds=1,
        **overrides,
    )


class FakeGitHub:
    def __init__(self) -> None:
        self.repositories: Sequence[GitHubRepository] = (
            GitHubRepository.model_validate(
                {
                    "id": 9001,
                    "owner": {"login": "octocat"},
                    "name": "fixture-repository",
                    "full_name": "octocat/fixture-repository",
                    "html_url": "https://github.com/octocat/fixture-repository",
                    "private": True,
                    "default_branch": "main",
                    "language": "Python",
                }
            ),
        )
        self.token_requests = 0

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://github.com/login/oauth/authorize?state={state}&code={code_challenge}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> SecretStr:
        del code, code_verifier
        return SecretStr("user-token")

    async def get_authenticated_user(self, access_token: SecretStr) -> GitHubUser:
        del access_token
        return GitHubUser(id=1, login="octocat")

    async def list_user_installations(
        self, access_token: SecretStr
    ) -> Sequence[GitHubInstallationData]:
        del access_token
        return ()

    async def create_installation_token(self, installation_id: int) -> SecretStr:
        assert installation_id == 501
        self.token_requests += 1
        return SecretStr("installation-token-sensitive-sentinel")

    async def list_installation_repositories(
        self, installation_token: SecretStr
    ) -> Sequence[GitHubRepository]:
        assert installation_token.get_secret_value() == "installation-token-sensitive-sentinel"
        return self.repositories

    async def close(self) -> None:
        return None


async def reset_dependencies() -> None:
    engine = create_async_engine(database_url())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE call_edges, chat_messages, chat_sessions, indexing_jobs, "
                "oauth_states, refresh_tokens, repositories, symbol_definitions, usage_records, "
                "installation_members, webhook_deliveries, github_installations, users CASCADE"
            )
        )
    await engine.dispose()
    redis = Redis.from_url(redis_url(), decode_responses=True)
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture(autouse=True)
def clean_dependencies() -> Iterator[None]:
    asyncio.run(reset_dependencies())
    yield
    asyncio.run(reset_dependencies())


async def seed_identity() -> tuple[uuid.UUID, uuid.UUID]:
    engine = create_async_engine(database_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = User(github_user_id=101, github_login="octocat")
        session.add(user)
        await session.flush()
        installation = GitHubInstallation(
            github_installation_id=501,
            account_type=GitHubAccountType.USER,
            account_github_id=101,
            account_login="octocat",
            installed_by_user_id=user.id,
            status=InstallationStatus.ACTIVE,
            permissions_json={"contents": "read", "metadata": "read"},
            repository_selection=RepositorySelection.SELECTED,
        )
        session.add(installation)
        await session.flush()
        session.add(
            InstallationMember(
                installation_id=installation.id,
                user_id=user.id,
                role=InstallationMemberRole.OWNER,
                verified_at=datetime.now(UTC),
            )
        )
        await session.commit()
        result = (user.id, installation.id)
    await engine.dispose()
    return result


@pytest.fixture
def api_runtime() -> Iterator[tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID]]:
    settings = integration_settings()
    user_id, installation_id = asyncio.run(seed_identity())
    github = FakeGitHub()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    queue = RedisJobQueue.from_settings(settings)
    app = create_app(
        settings=settings,
        database=database,
        github_client=github,
        job_queue=queue,
    )
    with TestClient(app) as client:
        yield client, settings, github, user_id, installation_id


def authorization(settings: Settings, user_id: uuid.UUID) -> dict[str, str]:
    token = TokenService(settings).issue_access_token(user_id).value
    return {"Authorization": f"Bearer {token}"}


def select_repository(
    client: TestClient,
    settings: Settings,
    user_id: uuid.UUID,
    installation_id: uuid.UUID,
) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    response = client.post(
        "/api/v1/repositories",
        headers=authorization(settings, user_id),
        json={"installation_id": str(installation_id), "github_repository_id": 9001},
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 202
    return response.json(), elapsed


async def scalar(statement: Any) -> Any:
    engine = create_async_engine(database_url())
    async with AsyncSession(engine) as session:
        value = await session.scalar(statement)
    await engine.dispose()
    return value


def create_git_fixture(root: Path, marker: Path) -> Path:
    fixture = root / "fixture"
    fixture.mkdir()
    subprocess.run(  # noqa: S603
        ["/usr/bin/git", "init", "-q", "-b", "main", str(fixture)],
        check=True,
    )
    (fixture / "safe.py").write_text("def answer():\n    return 42\n")
    (fixture / "README.md").write_text("# Controlled fixture\n")
    (fixture / "danger.py").write_text(f"open({str(marker)!r}, 'w').write('executed')\n")
    subprocess.run(  # noqa: S603
        ["/usr/bin/git", "-C", str(fixture), "add", "--", "."],
        check=True,
    )
    subprocess.run(  # noqa: S603
        [
            "/usr/bin/git",
            "-C",
            str(fixture),
            "-c",
            "user.name=RepoLume Test",
            "-c",
            "user.email=test@repolume.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
    )
    return fixture


class ControlledFixtureCloner:
    """Test-only local adapter; production always uses the github.com cloner."""

    def __init__(self, fixture: Path, temp_root: Path) -> None:
        self.fixture = fixture.resolve()
        self.temp_root = temp_root
        self.calls = 0
        self.failures: list[IndexingError] = []

    async def clone(self, request: CloneRequest) -> ClonedRepository:
        del request
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        workspace = Path(tempfile.mkdtemp(prefix="worker-fixture-", dir=self.temp_root))
        checkout = workspace / "checkout"
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/git",
            "clone",
            "--quiet",
            "--depth=1",
            "--single-branch",
            "--no-tags",
            "--no-recurse-submodules",
            "--no-local",
            "--branch",
            "main",
            "--",
            str(self.fixture),
            str(checkout),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert await process.wait() == 0
        revision = await asyncio.create_subprocess_exec(
            "/usr/bin/git",
            "-C",
            str(checkout),
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await revision.communicate()
        return ClonedRepository(
            workspace=workspace,
            checkout=checkout,
            commit_sha=stdout.decode().strip(),
        )

    @staticmethod
    def cleanup(cloned: ClonedRepository) -> None:
        shutil.rmtree(cloned.workspace)


async def process_two_deliveries(
    settings: Settings,
    github: FakeGitHub,
    cloner: ControlledFixtureCloner,
) -> None:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    queue = RedisJobQueue.from_settings(settings)
    worker = IndexingWorker(
        settings=settings,
        queue=queue,
        store=IndexingJobStore(database, settings),
        github=github,
        cloner=cloner,
        discovery=FileDiscovery(settings),
        worker_id="integration-worker",
    )
    await queue.ensure_group()
    first = await queue.receive("integration-worker")
    assert first is not None
    await worker.process_delivery(first)
    second = await queue.receive("integration-worker")
    assert second is not None
    await worker.process_delivery(second)
    await queue.close()
    await database.dispose()


def test_api_is_fast_idempotent_tenant_safe_and_queues_only_job_id(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
) -> None:
    client, settings, _, user_id, installation_id = api_runtime
    first, elapsed = select_repository(client, settings, user_id, installation_id)
    second, _ = select_repository(client, settings, user_id, installation_id)
    repository_id = first["repository"]["id"]
    job_id = first["job"]["job_id"]

    assert elapsed < 1
    assert first["job"]["job_status"] == "queued"
    assert second["job"]["job_id"] == job_id
    assert asyncio.run(scalar(select(func.count(IndexingJob.id)))) == 1

    async def stream_fields() -> list[dict[str, str]]:
        redis = Redis.from_url(redis_url(), decode_responses=True)
        rows = cast(
            list[tuple[str, dict[str, str]]],
            await redis.xrange(settings.worker_stream_name),
        )
        await redis.aclose()
        return [row[1] for row in rows]

    assert asyncio.run(stream_fields()) == [{"job_id": job_id}, {"job_id": job_id}]

    other_user = uuid.uuid4()

    async def add_other_user() -> None:
        engine = create_async_engine(database_url())
        async with AsyncSession(engine) as session:
            session.add(User(id=other_user, github_user_id=202, github_login="intruder"))
            await session.commit()
        await engine.dispose()

    asyncio.run(add_other_user())
    denied = client.get(
        f"/api/v1/repositories/{repository_id}",
        headers=authorization(settings, other_user),
    )
    assert denied.status_code == 404
    assert job_id not in denied.text


def test_real_worker_clones_controlled_fixture_without_execution_and_cleans_up(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, settings, github, user_id, installation_id = api_runtime
    marker = tmp_path / "must-not-exist"
    fixture = create_git_fixture(tmp_path, marker)
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)
    selected, _ = select_repository(client, settings, user_id, installation_id)
    select_repository(client, settings, user_id, installation_id)

    asyncio.run(process_two_deliveries(settings, github, cloner))

    status = client.get(
        f"/api/v1/repositories/{selected['repository']['id']}/status",
        headers=authorization(settings, user_id),
    )
    assert status.status_code == 200
    assert status.json()["job_status"] == "complete"
    assert status.json()["stage"] == "discovery_complete"
    assert status.json()["discovered_file_count"] == 3
    assert cloner.calls == 1
    assert tuple(clone_root.iterdir()) == ()
    assert not marker.exists()


def test_atomic_claim_retry_exhaustion_and_abandoned_recovery(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
) -> None:
    client, _, _, user_id, installation_id = api_runtime
    settings = integration_settings(worker_max_attempts=2)
    selected, _ = select_repository(client, settings, user_id, installation_id)
    job_id = uuid.UUID(selected["job"]["job_id"])

    async def exercise() -> tuple[int, bool, bool, int]:
        first_db = Database(engine=create_async_engine(database_url()), ready_timeout_seconds=2)
        second_db = Database(engine=create_async_engine(database_url()), ready_timeout_seconds=2)
        first_store = IndexingJobStore(first_db, settings)
        second_store = IndexingJobStore(second_db, settings)
        claims = await asyncio.gather(
            first_store.claim(job_id, "worker-a"),
            second_store.claim(job_id, "worker-b"),
        )
        claimed = next(item for item in claims if item is not None)
        retrying = await first_store.fail(
            claimed,
            "worker-a" if claims[0] is not None else "worker-b",
            code="clone_timeout",
            safe_message="Repository clone timed out",
            retryable=True,
        )
        async with first_db.session() as session:
            await session.execute(
                update(IndexingJob)
                .where(IndexingJob.id == job_id)
                .values(next_attempt_at=datetime.now(UTC))
            )
            await session.commit()
        claimed_again = await first_store.claim(job_id, "worker-c")
        assert claimed_again is not None
        final_retry = await first_store.fail(
            claimed_again,
            "worker-c",
            code="clone_timeout",
            safe_message="Repository clone timed out",
            retryable=True,
        )
        repository_id = uuid.UUID(selected["repository"]["id"])
        async with first_db.session() as session:
            abandoned = IndexingJob(
                repository_id=repository_id,
                requested_by_user_id=user_id,
                job_type=IndexingJobType.MANUAL_REINDEX,
                status=IndexingJobStatus.RUNNING,
                attempt=1,
                locked_by="dead-worker",
                heartbeat_at=datetime.now(UTC) - timedelta(minutes=1),
            )
            session.add(abandoned)
            await session.commit()
        recovered = await first_store.recover_abandoned()
        await first_db.dispose()
        await second_db.dispose()
        return sum(item is not None for item in claims), retrying, final_retry, recovered

    claim_count, retrying, final_retry, recovered = asyncio.run(exercise())
    assert claim_count == 1
    assert retrying is True
    assert final_retry is False
    assert recovered == 1


def test_worker_refuses_suspended_installation_before_clone(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, settings, github, user_id, installation_id = api_runtime
    selected, _ = select_repository(client, settings, user_id, installation_id)
    fixture = create_git_fixture(tmp_path, tmp_path / "marker")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)

    async def suspend_and_process() -> None:
        engine = create_async_engine(database_url())
        async with AsyncSession(engine) as session:
            await session.execute(
                update(GitHubInstallation)
                .where(GitHubInstallation.id == installation_id)
                .values(status=InstallationStatus.SUSPENDED, suspended_at=datetime.now(UTC))
            )
            await session.commit()
        await engine.dispose()
        database = Database(engine=create_async_engine(database_url()), ready_timeout_seconds=2)
        queue = RedisJobQueue.from_settings(settings)
        await queue.ensure_group()
        delivery = await queue.receive("revocation-worker")
        assert delivery is not None
        worker = IndexingWorker(
            settings=settings,
            queue=queue,
            store=IndexingJobStore(database, settings),
            github=github,
            cloner=cloner,
            discovery=FileDiscovery(settings),
            worker_id="revocation-worker",
        )
        await worker.process_delivery(delivery)
        await queue.close()
        await database.dispose()

    asyncio.run(suspend_and_process())
    status = asyncio.run(
        scalar(
            select(IndexingJob.status).where(IndexingJob.id == uuid.UUID(selected["job"]["job_id"]))
        )
    )
    assert status == IndexingJobStatus.CANCELLED
    assert cloner.calls == 0
