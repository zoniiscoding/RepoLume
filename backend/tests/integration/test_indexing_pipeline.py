"""PostgreSQL, Redis, API, worker, and controlled Git fixture integration tests."""

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.application import create_app
from app.auth.tokens import TokenService
from app.core.config import Settings
from app.db.models.call_edge import CallEdge
from app.db.models.enums import (
    GitHubAccountType,
    IndexBuildState,
    IndexCleanupStatus,
    IndexingJobStatus,
    IndexingJobType,
    InstallationMemberRole,
    InstallationStatus,
    RepositorySelection,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.models.repository_index_build import RepositoryIndexBuild
from app.db.models.symbol_definition import SymbolDefinition
from app.db.models.user import User
from app.db.models.webhook_delivery import WebhookDelivery
from app.db.session import Database
from app.embeddings.client import EmbeddingProviderProtocol, EmbeddingServiceClient
from app.embeddings.preprocessing import PreparedEmbedding
from app.github.client import PublicGitHubRepository
from app.github.schemas import (
    GitHubCommitComparison,
    GitHubHistoryBundle,
    GitHubRepository,
    GitHubUser,
)
from app.github.schemas import GitHubInstallation as GitHubInstallationData
from app.indexing.analyzer import ProcessIsolatedAnalyzer
from app.indexing.clone import ClonedRepository, CloneRequest
from app.indexing.discovery import DiscoveryResult, FileDiscovery
from app.indexing.failures import IndexingError
from app.indexing.models import ProcessingResult
from app.indexing.worker import IndexingWorker
from app.queue import RedisJobQueue
from app.services.indexing_jobs import IndexingJobStore
from app.vector.qdrant import QdrantVectorStore, VectorScope
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


def qdrant_url() -> str:
    value = os.environ.get("TEST_QDRANT_URL")
    if value is None:
        pytest.fail("TEST_QDRANT_URL must target a disposable Qdrant instance")
    return value


def embedding_service_url() -> str:
    value = os.environ.get("TEST_EMBEDDING_SERVICE_URL")
    if value is None:
        pytest.fail("TEST_EMBEDDING_SERVICE_URL must target the private embedding service")
    return value


def embedding_service_token() -> str:
    value = os.environ.get("TEST_EMBEDDING_SERVICE_TOKEN")
    if value is None:
        pytest.fail("TEST_EMBEDDING_SERVICE_TOKEN must authenticate the private embedding service")
    return value


def integration_settings(**overrides: object) -> Settings:
    return make_settings(
        database_url=database_url(),
        redis_url=redis_url(),
        qdrant_url=qdrant_url(),
        qdrant_collection_name="repolume_test_chunks",
        # Keep integration deliveries separate from a developer's normal worker.
        # The queue is a shared external service in local runs, and a production
        # consumer group on the same stream can otherwise claim a test wakeup.
        worker_stream_name="repolume:test:indexing",
        worker_consumer_group="repolume-test-workers",
        worker_poll_timeout_ms=100,
        worker_abandoned_after_seconds=5,
        worker_retry_base_seconds=1,
        worker_retry_max_seconds=1,
        rag_retrieval_score_threshold=0.0,
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
        self.compare_status: Literal["ahead", "behind", "diverged", "identical"] = "ahead"
        self.compare_files: list[dict[str, object]] = []

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

    async def create_repository_installation_token(
        self, installation_id: int, *, repository_id: int
    ) -> SecretStr:
        assert repository_id == 9001
        return await self.create_installation_token(installation_id)

    async def compare_repository_commits(
        self,
        installation_token: SecretStr,
        *,
        owner: str,
        repository: str,
        base: str,
        head: str,
    ) -> GitHubCommitComparison:
        del installation_token, owner, repository, base, head
        return GitHubCommitComparison.model_validate(
            {
                "status": self.compare_status,
                "ahead_by": 1,
                "behind_by": 0,
                "total_commits": 1,
                "files": self.compare_files,
            }
        )

    async def list_installation_repositories(
        self, installation_token: SecretStr
    ) -> Sequence[GitHubRepository]:
        assert installation_token.get_secret_value() == "installation-token-sensitive-sentinel"
        return self.repositories

    async def get_public_repository(self, *, owner: str, repository: str) -> PublicGitHubRepository:
        del owner, repository
        raise AssertionError("public repository access is outside this fixture")

    async def compare_public_repository_commits(
        self, *, owner: str, repository: str, base: str, head: str
    ) -> GitHubCommitComparison:
        del owner, repository, base, head
        raise AssertionError("public repository access is outside this fixture")

    async def get_public_repository_history(
        self, *, owner: str, repository: str, revision: str, limit: int
    ) -> Sequence[GitHubHistoryBundle]:
        del owner, repository, revision, limit
        raise AssertionError("public repository access is outside this fixture")

    async def close(self) -> None:
        return None


async def reset_dependencies() -> None:
    engine = create_async_engine(database_url())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE call_edges, chat_messages, chat_sessions, "
                "repository_index_builds, indexing_jobs, "
                "oauth_states, refresh_tokens, repositories, symbol_definitions, usage_records, "
                "installation_members, webhook_deliveries, github_installations, users CASCADE"
            )
        )
    await engine.dispose()
    redis = Redis.from_url(redis_url(), decode_responses=True)
    await redis.flushdb()
    await redis.aclose()
    qdrant = AsyncQdrantClient(url=qdrant_url())
    if await qdrant.collection_exists("repolume_test_chunks"):
        await qdrant.delete_collection("repolume_test_chunks")
    await qdrant.close()


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
    vectors = QdrantVectorStore(settings)
    app = create_app(
        settings=settings,
        database=database,
        github_client=github,
        job_queue=queue,
        vector_store=vectors,
        embedding_provider=DeterministicEmbeddings(),
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


async def _all_jobs_for_repository(repository_id: uuid.UUID) -> list[IndexingJob]:
    engine = create_async_engine(database_url())
    async with AsyncSession(engine) as session:
        jobs = list(
            (
                await session.scalars(
                    select(IndexingJob)
                    .where(IndexingJob.repository_id == repository_id)
                    .order_by(IndexingJob.created_at)
                )
            ).all()
        )
    await engine.dispose()
    return jobs


def create_git_fixture(root: Path, marker: Path) -> Path:
    fixture = root / "fixture"
    fixture.mkdir()
    subprocess.run(  # noqa: S603
        ["/usr/bin/git", "init", "-q", "-b", "main", str(fixture)],
        check=True,
    )
    (fixture / "safe.py").write_text(
        "def answer():\n    return 42\n\ndef call_answer():\n    return answer()\n"
    )
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


class FailingAnalyzer:
    async def analyze(
        self,
        *,
        checkout: Path,
        discovery: DiscoveryResult,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
        on_chunking: Callable[[], Awaitable[None]],
        on_graphing: Callable[[], Awaitable[None]] | None = None,
    ) -> ProcessingResult:
        del checkout, discovery, repository_id, index_version, commit_sha, on_chunking, on_graphing
        raise IndexingError(
            code="internal_parser_failure",
            message="Static repository processing failed safely",
            retryable=False,
        )


class DeterministicEmbeddings:
    """Content-free deterministic adapter; the service package tests the real model."""

    async def is_ready(self) -> bool:
        return True

    async def embed_documents(
        self, documents: Sequence[PreparedEmbedding]
    ) -> dict[str, tuple[float, ...]]:
        results: dict[str, tuple[float, ...]] = {}
        for document in documents:
            vector = [0.0] * 768
            vector[0 if "answer" in document.text else 1] = 1.0
            results[document.item_id] = tuple(vector)
        return results

    async def embed_query(self, query: PreparedEmbedding) -> tuple[float, ...]:
        vector = [0.0] * 768
        vector[0 if "answer" in query.text else 1] = 1.0
        return tuple(vector)

    async def close(self) -> None:
        return None


class FailingEmbeddings(DeterministicEmbeddings):
    async def embed_documents(
        self, documents: Sequence[PreparedEmbedding]
    ) -> dict[str, tuple[float, ...]]:
        del documents
        raise IndexingError(
            code="embedding_generation_failed",
            message="Embedding generation failed safely",
            retryable=False,
        )


async def process_two_deliveries(
    settings: Settings,
    github: FakeGitHub,
    cloner: ControlledFixtureCloner,
    embeddings: EmbeddingProviderProtocol | None = None,
) -> None:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    queue = RedisJobQueue.from_settings(settings)
    embedding_provider = embeddings or DeterministicEmbeddings()
    vectors = QdrantVectorStore(settings)
    worker = IndexingWorker(
        settings=settings,
        queue=queue,
        store=IndexingJobStore(database, settings),
        github=github,
        cloner=cloner,
        discovery=FileDiscovery(settings),
        analyzer=ProcessIsolatedAnalyzer(settings),
        embeddings=embedding_provider,
        vectors=vectors,
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
    await embedding_provider.close()
    await vectors.close()
    await database.dispose()


async def process_one_delivery(
    settings: Settings,
    github: FakeGitHub,
    cloner: ControlledFixtureCloner,
) -> None:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    queue = RedisJobQueue.from_settings(settings)
    vectors = QdrantVectorStore(settings)
    worker = IndexingWorker(
        settings=settings,
        queue=queue,
        store=IndexingJobStore(database, settings),
        github=github,
        cloner=cloner,
        discovery=FileDiscovery(settings),
        analyzer=ProcessIsolatedAnalyzer(settings),
        embeddings=DeterministicEmbeddings(),
        vectors=vectors,
        worker_id="freshness-integration-worker",
    )
    await queue.ensure_group()
    delivery = await queue.receive("freshness-integration-worker")
    assert delivery is not None
    await worker.process_delivery(delivery)
    await queue.close()
    await vectors.close()
    await database.dispose()


async def enqueue_manual_reindex(
    settings: Settings,
    github: FakeGitHub,
    cloner: ControlledFixtureCloner,
    repository_id: uuid.UUID,
    user_id: uuid.UUID,
    embeddings: DeterministicEmbeddings,
) -> uuid.UUID:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    async with database.session() as session:
        job = IndexingJob(
            repository_id=repository_id,
            requested_by_user_id=user_id,
            job_type=IndexingJobType.MANUAL_REINDEX,
            status=IndexingJobStatus.QUEUED,
            stage="queued",
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    queue = RedisJobQueue.from_settings(settings)
    vectors = QdrantVectorStore(settings)
    await queue.ensure_group()
    await queue.enqueue(job_id)
    delivery = await queue.receive("replacement-failure-worker")
    assert delivery is not None
    worker = IndexingWorker(
        settings=settings,
        queue=queue,
        store=IndexingJobStore(database, settings),
        github=github,
        cloner=cloner,
        discovery=FileDiscovery(settings),
        analyzer=ProcessIsolatedAnalyzer(settings),
        embeddings=embeddings,
        vectors=vectors,
        worker_id="replacement-failure-worker",
    )
    await worker.process_delivery(delivery)
    await queue.close()
    await vectors.close()
    await database.dispose()
    return job_id


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


def test_signed_push_incrementally_activates_complete_new_version_and_replay_is_stale(  # noqa: PLR0915 -- one end-to-end freshness contract
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
    repository_id = uuid.UUID(selected["repository"]["id"])
    commit_a = str(asyncio.run(scalar(select(Repository.last_indexed_commit_sha))))
    question_headers = authorization(settings, user_id)
    answer_at_a = client.post(
        f"/api/v1/repositories/{repository_id}/questions",
        headers=question_headers,
        json={"question": "def answer return 42"},
    )
    assert answer_at_a.status_code == 200
    assert answer_at_a.json()["indexed_commit_sha"] == commit_a
    assert answer_at_a.json()["citations"], answer_at_a.json()
    assert {item["file_path"] for item in answer_at_a.json()["citations"]} == {"safe.py"}

    subprocess.run(  # noqa: S603
        ["/usr/bin/git", "-C", str(fixture), "mv", "safe.py", "renamed.py"],
        check=True,
    )
    (fixture / "renamed.py").write_text(
        "def answer():\n    return 43\n\ndef call_answer():\n    return answer() + 1\n"
    )
    (fixture / "added.py").write_text(
        "from renamed import answer\n\ndef use_answer():\n    return answer()\n"
    )
    (fixture / "README.md").unlink()
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
            "fixture refresh",
        ],
        check=True,
    )
    revision = subprocess.run(  # noqa: S603
        ["/usr/bin/git", "-C", str(fixture), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    commit_b = revision.stdout.strip()
    github.compare_files = [
        {
            "filename": "renamed.py",
            "previous_filename": "safe.py",
            "status": "renamed",
            "changes": 4,
        },
        {"filename": "added.py", "status": "added", "changes": 3},
        {"filename": "README.md", "status": "removed", "changes": 1},
    ]
    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "before": commit_a,
            "after": commit_b,
            "forced": False,
            "deleted": False,
            "installation": {
                "id": 501,
                "account": {"id": 1, "login": "octocat", "type": "User"},
                "permissions": {"contents": "read", "metadata": "read"},
                "repository_selection": "selected",
                "suspended_at": None,
            },
            "repository": {
                "id": 9001,
                "owner": {"login": "octocat"},
                "name": "fixture-repository",
                "full_name": "octocat/fixture-repository",
                "html_url": "https://github.com/octocat/fixture-repository",
                "private": True,
                "default_branch": "main",
            },
        },
        separators=(",", ":"),
    ).encode()
    signature = (
        "sha256="
        + hmac.new(
            settings.github_webhook_secret.get_secret_value().encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "m9-a-to-b",
        },
    )
    assert response.status_code == 202
    before_processing = client.get(
        f"/api/v1/repositories/{repository_id}/status",
        headers=authorization(settings, user_id),
    ).json()
    assert before_processing["active_commit_sha"] == commit_a
    assert before_processing["searchable"] is True
    answer_while_building = client.post(
        f"/api/v1/repositories/{repository_id}/questions",
        headers=question_headers,
        json={"question": "def answer return 42"},
    )
    assert answer_while_building.status_code == 200
    assert answer_while_building.json()["indexed_commit_sha"] == commit_a
    assert {item["file_path"] for item in answer_while_building.json()["citations"]} == {"safe.py"}

    asyncio.run(process_one_delivery(settings, github, cloner))

    assert (
        asyncio.run(
            scalar(select(WebhookDelivery.status).where(WebhookDelivery.delivery_id == "m9-a-to-b"))
        )
        == "completed"
    )

    after_processing = client.get(
        f"/api/v1/repositories/{repository_id}/status",
        headers=authorization(settings, user_id),
    ).json()
    assert after_processing["active_commit_sha"] == commit_b
    assert after_processing["active_index_version"] == 2
    assert after_processing["actual_mode"] == "incremental"
    assert after_processing["changed_file_counts"] == {"added": 1, "removed": 1, "renamed": 1}
    assert after_processing["reused_chunk_count"] >= 1
    assert after_processing["reembedded_chunk_count"] >= 1
    assert after_processing["graph_rebuilt"] is True
    answer_at_b = client.post(
        f"/api/v1/repositories/{repository_id}/questions",
        headers=question_headers,
        json={"question": "def answer return 43"},
    )
    assert answer_at_b.status_code == 200
    assert answer_at_b.json()["indexed_commit_sha"] == commit_b
    answer_b_paths = {item["file_path"] for item in answer_at_b.json()["citations"]}
    assert "renamed.py" in answer_b_paths
    assert "safe.py" not in answer_b_paths
    callers_at_b = client.post(
        f"/api/v1/repositories/{repository_id}/questions",
        headers=question_headers,
        json={"question": "What calls answer?"},
    )
    assert callers_at_b.status_code == 200
    caller_paths = {item["caller_file_path"] for item in callers_at_b.json()["citations"]}
    assert caller_paths == {"added.py", "renamed.py"}
    assert "safe.py" not in callers_at_b.text
    assert (
        asyncio.run(
            scalar(
                select(func.count(SymbolDefinition.id)).where(
                    SymbolDefinition.repository_id == repository_id,
                    SymbolDefinition.index_version == 2,
                    SymbolDefinition.file_path == "safe.py",
                )
            )
        )
        == 0
    )
    assert (
        asyncio.run(
            scalar(
                select(func.count(SymbolDefinition.id)).where(
                    SymbolDefinition.repository_id == repository_id,
                    SymbolDefinition.index_version == 2,
                    SymbolDefinition.file_path == "renamed.py",
                )
            )
        )
        > 0
    )
    assert marker.exists() is False

    replay = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "m9-stale-replay",
        },
    )
    assert replay.status_code == 202
    assert (
        asyncio.run(
            scalar(
                select(WebhookDelivery.status).where(
                    WebhookDelivery.delivery_id == "m9-stale-replay"
                )
            )
        )
        == "stale"
    )
    assert asyncio.run(scalar(select(func.count(IndexingJob.id)))) == 2


def test_manual_reindex_supersedes_older_queued_generation_without_conflicting_activation(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, settings, github, user_id, installation_id = api_runtime
    fixture = create_git_fixture(tmp_path, tmp_path / "must-not-exist")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)
    selected, _ = select_repository(client, settings, user_id, installation_id)
    repository_id = selected["repository"]["id"]

    manual = client.post(
        f"/api/v1/repositories/{repository_id}/reindex",
        headers=authorization(settings, user_id),
    )
    assert manual.status_code == 202
    assert manual.json()["job"]["requested_mode"] == "full"
    asyncio.run(process_two_deliveries(settings, github, cloner))

    jobs = asyncio.run(_all_jobs_for_repository(uuid.UUID(repository_id)))
    assert [job.status for job in jobs] == [
        IndexingJobStatus.CANCELLED,
        IndexingJobStatus.COMPLETE,
    ]
    assert jobs[0].error_code == "refresh_superseded"
    assert jobs[1].actual_mode is not None
    assert jobs[1].actual_mode.value == "full"


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
    assert status.json()["stage"] == "complete"
    assert status.json()["discovered_file_count"] == 3
    assert status.json()["parsed_file_count"] == 3
    assert status.json()["symbol_count"] == 2
    assert status.json()["chunk_count"] == 4
    assert status.json()["embedded_chunk_count"] == 4
    assert status.json()["vector_count"] == 4
    assert status.json()["active_vector_count"] == 4
    assert status.json()["call_site_count"] == 1
    assert status.json()["exact_edge_count"] == 1
    assert status.json()["ambiguous_edge_count"] == 0
    assert status.json()["unresolved_call_count"] == 0
    assert status.json()["graph_warning_count"] == 0
    assert status.json()["active_index_version"] == 1
    assert status.json()["searchable"] is True
    assert asyncio.run(scalar(select(func.count(SymbolDefinition.id)))) == 2
    assert asyncio.run(scalar(select(func.count(CallEdge.id)))) == 1
    assert asyncio.run(scalar(select(SymbolDefinition.index_version))) == 1
    assert asyncio.run(scalar(select(func.count(RepositoryIndexBuild.id)))) == 1
    repository = asyncio.run(
        scalar(select(Repository).where(Repository.id == uuid.UUID(selected["repository"]["id"])))
    )
    assert repository is not None

    async def vector_count() -> int:
        vectors = QdrantVectorStore(settings)
        count = await vectors.count_scope(
            VectorScope(installation_id, repository.id, repository.index_version)
        )
        await vectors.close()
        return count

    assert asyncio.run(vector_count()) == 4
    assert cloner.calls == 1
    assert tuple(clone_root.iterdir()) == ()
    assert not marker.exists()


def test_real_embedding_service_completes_controlled_fixture_pipeline(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, _, github, user_id, installation_id = api_runtime
    settings = integration_settings(
        embedding_service_url=embedding_service_url(),
        embedding_service_token=embedding_service_token(),
    )
    fixture = create_git_fixture(tmp_path, tmp_path / "must-not-exist")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)
    selected, _ = select_repository(client, settings, user_id, installation_id)
    select_repository(client, settings, user_id, installation_id)

    asyncio.run(
        process_two_deliveries(
            settings,
            github,
            cloner,
            embeddings=EmbeddingServiceClient(settings),
        )
    )

    response = client.get(
        f"/api/v1/repositories/{selected['repository']['id']}/status",
        headers=authorization(settings, user_id),
    )
    assert response.status_code == 200
    assert response.json()["job_status"] == "complete"
    assert response.json()["active_index_version"] == 1
    assert response.json()["embedded_chunk_count"] == 4
    assert response.json()["active_vector_count"] == 4
    assert response.json()["call_site_count"] == 1
    assert response.json()["searchable"] is True
    assert tuple(clone_root.iterdir()) == ()


def test_parsing_failure_is_safe_and_still_cleans_temporary_clone(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, settings, github, user_id, installation_id = api_runtime
    selected, _ = select_repository(client, settings, user_id, installation_id)
    fixture = create_git_fixture(tmp_path, tmp_path / "must-not-exist")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)

    async def process() -> None:
        database = Database(
            engine=create_async_engine(database_url(), pool_pre_ping=True),
            ready_timeout_seconds=2,
        )
        queue = RedisJobQueue.from_settings(settings)
        vectors = QdrantVectorStore(settings)
        await queue.ensure_group()
        delivery = await queue.receive("parser-failure-worker")
        assert delivery is not None
        worker = IndexingWorker(
            settings=settings,
            queue=queue,
            store=IndexingJobStore(database, settings),
            github=github,
            cloner=cloner,
            discovery=FileDiscovery(settings),
            analyzer=FailingAnalyzer(),
            embeddings=DeterministicEmbeddings(),
            vectors=vectors,
            worker_id="parser-failure-worker",
        )
        await worker.process_delivery(delivery)
        await queue.close()
        await vectors.close()
        await database.dispose()

    asyncio.run(process())
    response = client.get(
        f"/api/v1/repositories/{selected['repository']['id']}/status",
        headers=authorization(settings, user_id),
    )
    assert response.status_code == 200
    assert response.json()["job_status"] == "failed"
    assert response.json()["error_code"] == "internal_parser_failure"
    assert response.json()["safe_error_message"] == ("Static repository processing failed safely")
    assert tuple(clone_root.iterdir()) == ()


def test_failed_replacement_preserves_previous_active_index(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, settings, github, user_id, installation_id = api_runtime
    fixture = create_git_fixture(tmp_path, tmp_path / "must-not-exist")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)
    selected, _ = select_repository(client, settings, user_id, installation_id)
    select_repository(client, settings, user_id, installation_id)
    asyncio.run(process_two_deliveries(settings, github, cloner))
    repository_id = uuid.UUID(selected["repository"]["id"])

    failed_job_id = asyncio.run(
        enqueue_manual_reindex(
            settings,
            github,
            cloner,
            repository_id,
            user_id,
            FailingEmbeddings(),
        )
    )

    response = client.get(
        f"/api/v1/repositories/{repository_id}/status",
        headers=authorization(settings, user_id),
    )
    assert response.status_code == 200
    status_payload = response.json()
    assert status_payload["job_id"] == str(failed_job_id)
    assert status_payload["job_status"] == "failed"
    assert status_payload["error_code"] == "embedding_generation_failed"
    assert status_payload["active_index_version"] == 1
    assert status_payload["vector_count"] == 0
    assert status_payload["active_vector_count"] == 4
    assert status_payload["searchable"] is True

    async def durable_state() -> tuple[int, tuple[tuple[IndexBuildState, IndexCleanupStatus], ...]]:
        engine = create_async_engine(database_url())
        async with AsyncSession(engine) as session:
            repository = await session.get(Repository, repository_id)
            assert repository is not None
            rows = (
                await session.execute(
                    select(RepositoryIndexBuild.state, RepositoryIndexBuild.cleanup_status)
                    .where(RepositoryIndexBuild.repository_id == repository_id)
                    .order_by(RepositoryIndexBuild.index_version)
                )
            ).all()
            builds = tuple((row.state, row.cleanup_status) for row in rows)
            active_vectors = repository.active_vector_count
        await engine.dispose()
        return active_vectors, builds

    active_vectors, builds = asyncio.run(durable_state())
    assert active_vectors == 4
    assert builds == (
        (IndexBuildState.ACTIVE, IndexCleanupStatus.NOT_REQUIRED),
        (IndexBuildState.FAILED, IndexCleanupStatus.COMPLETE),
    )

    async def vector_counts() -> tuple[int, int]:
        vectors = QdrantVectorStore(settings)
        active = await vectors.count_scope(VectorScope(installation_id, repository_id, 1))
        failed = await vectors.count_scope(VectorScope(installation_id, repository_id, 2))
        await vectors.close()
        return active, failed

    assert asyncio.run(vector_counts()) == (4, 0)
    assert (
        asyncio.run(scalar(select(func.count(CallEdge.id)).where(CallEdge.index_version == 1))) == 1
    )
    assert (
        asyncio.run(scalar(select(func.count(CallEdge.id)).where(CallEdge.index_version == 2))) == 0
    )
    assert tuple(clone_root.iterdir()) == ()


def test_successful_replacement_activates_then_cleans_previous_version(
    api_runtime: tuple[TestClient, Settings, FakeGitHub, uuid.UUID, uuid.UUID],
    tmp_path: Path,
) -> None:
    client, settings, github, user_id, installation_id = api_runtime
    fixture = create_git_fixture(tmp_path, tmp_path / "must-not-exist")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()
    cloner = ControlledFixtureCloner(fixture, clone_root)
    selected, _ = select_repository(client, settings, user_id, installation_id)
    select_repository(client, settings, user_id, installation_id)
    asyncio.run(process_two_deliveries(settings, github, cloner))
    repository_id = uuid.UUID(selected["repository"]["id"])
    asyncio.run(
        enqueue_manual_reindex(
            settings,
            github,
            cloner,
            repository_id,
            user_id,
            DeterministicEmbeddings(),
        )
    )

    response = client.get(
        f"/api/v1/repositories/{repository_id}/status",
        headers=authorization(settings, user_id),
    )
    assert response.status_code == 200
    assert response.json()["job_status"] == "complete"
    assert response.json()["active_index_version"] == 2
    assert response.json()["active_vector_count"] == 4
    assert response.json()["searchable"] is True

    async def durable_state() -> tuple[list[tuple[IndexBuildState, IndexCleanupStatus]], int]:
        engine = create_async_engine(database_url())
        async with AsyncSession(engine) as session:
            rows = (
                await session.execute(
                    select(RepositoryIndexBuild.state, RepositoryIndexBuild.cleanup_status)
                    .where(RepositoryIndexBuild.repository_id == repository_id)
                    .order_by(RepositoryIndexBuild.index_version)
                )
            ).all()
            old_symbols = await session.scalar(
                select(func.count(SymbolDefinition.id)).where(
                    SymbolDefinition.repository_id == repository_id,
                    SymbolDefinition.index_version == 1,
                )
            )
        await engine.dispose()
        return [(row.state, row.cleanup_status) for row in rows], old_symbols or 0

    builds, old_symbols = asyncio.run(durable_state())
    assert builds == [
        (IndexBuildState.SUPERSEDED, IndexCleanupStatus.COMPLETE),
        (IndexBuildState.ACTIVE, IndexCleanupStatus.NOT_REQUIRED),
    ]
    assert old_symbols == 0

    async def vector_counts() -> tuple[int, int]:
        vectors = QdrantVectorStore(settings)
        old = await vectors.count_scope(VectorScope(installation_id, repository_id, 1))
        active = await vectors.count_scope(VectorScope(installation_id, repository_id, 2))
        await vectors.close()
        return old, active

    assert asyncio.run(vector_counts()) == (0, 4)
    assert (
        asyncio.run(scalar(select(func.count(CallEdge.id)).where(CallEdge.index_version == 1))) == 0
    )
    assert (
        asyncio.run(scalar(select(func.count(CallEdge.id)).where(CallEdge.index_version == 2))) == 1
    )
    assert tuple(clone_root.iterdir()) == ()


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
        vectors = QdrantVectorStore(settings)
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
            analyzer=ProcessIsolatedAnalyzer(settings),
            embeddings=DeterministicEmbeddings(),
            vectors=vectors,
            worker_id="revocation-worker",
        )
        await worker.process_delivery(delivery)
        await queue.close()
        await vectors.close()
        await database.dispose()

    asyncio.run(suspend_and_process())
    status = asyncio.run(
        scalar(
            select(IndexingJob.status).where(IndexingJob.id == uuid.UUID(selected["job"]["job_id"]))
        )
    )
    assert status == IndexingJobStatus.CANCELLED
    assert cloner.calls == 0
