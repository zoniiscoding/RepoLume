"""PostgreSQL-backed multi-provider authentication and public repository flows."""

import asyncio
import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.sql import Executable

from app.application import create_app
from app.auth.cookies import AUTH_COOKIE_PATH, OIDC_NONCE_COOKIE_NAME, PKCE_COOKIE_NAME
from app.auth.google import GoogleIdentity
from app.auth.tokens import TokenService
from app.db.models.auth import AuthIdentity
from app.db.models.enums import (
    AuthProvider,
    IndexBuildState,
    IndexCleanupStatus,
    IndexingJobStatus,
    RepositoryIndexingStatus,
)
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.models.repository_index_build import RepositoryIndexBuild
from app.db.models.user import User
from app.db.models.user_repository import UserRepository
from app.db.session import Database
from app.github.client import (
    GitHubRepositoryPrivateError,
    PublicGitHubRepository,
)
from app.github.schemas import GitHubCommitComparison, GitHubHistoryBundle, GitHubRepository
from tests.conftest import FakeJobQueue, make_settings
from tests.integration.test_auth_github import FakeGitHubClient

pytestmark = pytest.mark.integration


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.fail("TEST_DATABASE_URL must target a disposable PostgreSQL database")
    return value


async def _reset_database() -> None:
    engine = create_async_engine(_database_url())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE users, oauth_states, repositories, indexing_jobs, "
                "repository_index_builds CASCADE"
            )
        )
    await engine.dispose()


class FakeGoogleClient:
    def __init__(self) -> None:
        self.identity = GoogleIdentity(
            subject="google-subject-1",
            email="google-user@example.test",
            email_verified=True,
            display_name="Google User",
            avatar_url="https://lh3.googleusercontent.com/avatar",
        )
        self.expected_nonce: str | None = None

    def authorization_url(self, *, state: str, code_challenge: str, nonce: str) -> str:
        self.expected_nonce = nonce
        return (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?state={state}&code_challenge={code_challenge}&nonce={nonce}"
        )

    async def authenticate(
        self, *, code: str, code_verifier: str, expected_nonce: str
    ) -> GoogleIdentity:
        assert code == "google-code-sensitive-sentinel"
        assert len(code_verifier) >= 43
        assert expected_nonce == self.expected_nonce
        return self.identity

    async def close(self) -> None:
        return None


class PublicFakeGitHub(FakeGitHubClient):
    def __init__(self) -> None:
        super().__init__()
        self.public_sha = "a" * 40
        self.public_private = False
        self.public_repository = GitHubRepository.model_validate(
            {
                "id": 9200,
                "owner": {"login": "octocat"},
                "name": "public-repo",
                "full_name": "octocat/public-repo",
                "html_url": "https://github.com/octocat/public-repo",
                "private": False,
                "default_branch": "main",
                "language": "Python",
                "size": 128,
            }
        )

    async def get_public_repository(self, *, owner: str, repository: str) -> PublicGitHubRepository:
        assert owner == self.public_repository.owner.login
        assert repository == self.public_repository.name
        if self.public_private:
            raise GitHubRepositoryPrivateError
        return PublicGitHubRepository(self.public_repository, self.public_sha)

    async def compare_public_repository_commits(
        self, *, owner: str, repository: str, base: str, head: str
    ) -> GitHubCommitComparison:
        del owner, repository, base, head
        return GitHubCommitComparison(
            status="ahead", ahead_by=1, behind_by=0, total_commits=1, files=[]
        )

    async def get_public_repository_history(
        self, *, owner: str, repository: str, revision: str, limit: int
    ) -> Sequence[GitHubHistoryBundle]:
        del owner, repository, revision, limit
        return ()


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    asyncio.run(_reset_database())
    yield
    asyncio.run(_reset_database())


@pytest.fixture
def application_client() -> Iterator[
    tuple[TestClient, PublicFakeGitHub, FakeGoogleClient, FakeJobQueue, TokenService]
]:
    settings = make_settings(
        frontend_url=None,
        google_auth_enabled=True,
        google_client_id="google-client-id.apps.googleusercontent.com",
        google_client_secret="google-client-secret-for-tests-only-0000000",  # noqa: S106
        google_oauth_callback_url="http://testserver/api/v1/auth/google/callback",
    )
    github = PublicFakeGitHub()
    google = FakeGoogleClient()
    queue = FakeJobQueue()
    database = Database(
        engine=create_async_engine(_database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=github,
        google_client=google,
        job_queue=queue,
    )
    with TestClient(app) as client:
        yield client, github, google, queue, TokenService(settings)


def _start(client: TestClient, provider: str, headers: dict[str, str] | None = None) -> str:
    response = client.get(
        f"/api/v1/auth/{provider}/start"
        if headers is None
        else f"/api/v1/auth/link/{provider}/start",
        headers=headers,
        follow_redirects=False,
    )
    assert response.status_code == 307
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _google_login(client: TestClient) -> tuple[str, uuid.UUID]:
    state = _start(client, "google")
    assert client.cookies.get(PKCE_COOKIE_NAME, path=AUTH_COOKIE_PATH)
    assert client.cookies.get(OIDC_NONCE_COOKIE_NAME, path=AUTH_COOKIE_PATH)
    response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code-sensitive-sentinel", "state": state},
    )
    assert response.status_code == 200
    return response.json()["access_token"], uuid.UUID(response.json()["user"]["id"])


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_google_state_replay_and_explicit_github_linking(
    application_client: tuple[
        TestClient, PublicFakeGitHub, FakeGoogleClient, FakeJobQueue, TokenService
    ],
) -> None:
    client, _, _, _, _ = application_client
    state = _start(client, "google")
    response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code-sensitive-sentinel", "state": state},
    )
    assert response.status_code == 200
    user_id = response.json()["user"]["id"]
    assert response.json()["user"]["linked_providers"] == ["google"]
    replay = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code-sensitive-sentinel", "state": state},
    )
    assert replay.status_code == 400

    access_token = response.json()["access_token"]
    link_state = _start(client, "github", _authorization(access_token))
    linked = client.get(
        "/api/v1/auth/github/callback",
        params={"code": "github-link-code", "state": link_state},
    )
    assert linked.status_code == 200
    assert linked.json()["user"]["id"] == user_id
    assert linked.json()["user"]["linked_providers"] == ["github", "google"]


def test_verified_email_match_requires_explicit_link_without_merging(
    application_client: tuple[
        TestClient, PublicFakeGitHub, FakeGoogleClient, FakeJobQueue, TokenService
    ],
) -> None:
    client, _, google, _, _ = application_client

    async def seed() -> uuid.UUID:
        engine = create_async_engine(_database_url())
        try:
            async with AsyncSession(engine) as session:
                user = User(display_name="Existing")
                session.add(user)
                await session.flush()
                user_id = user.id
                session.add(
                    AuthIdentity(
                        user_id=user.id,
                        provider=AuthProvider.GITHUB,
                        provider_subject="999",
                        provider_email=google.identity.email,
                        email_verified=True,
                    )
                )
                await session.commit()
                return user_id
        finally:
            await engine.dispose()

    existing_id = asyncio.run(seed())
    state = _start(client, "google")
    response = client.get(
        "/api/v1/auth/google/callback",
        params={"code": "google-code-sensitive-sentinel", "state": state},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "identity_link_required"
    user_count = asyncio.run(_scalar(select(func.count()).select_from(User)))
    assert user_count == 1
    assert asyncio.run(_scalar(select(User.id))) == existing_id


async def _scalar(statement: Executable) -> object:
    engine = create_async_engine(_database_url())
    async with AsyncSession(engine) as session:
        value = await session.scalar(statement)
    await engine.dispose()
    return value


def test_public_import_is_shared_but_membership_remains_user_scoped(
    application_client: tuple[
        TestClient, PublicFakeGitHub, FakeGoogleClient, FakeJobQueue, TokenService
    ],
) -> None:
    client, _, google, queue, tokens = application_client
    first_token, first_user_id = _google_login(client)
    first = client.post(
        "/api/v1/public-repositories/import",
        headers=_authorization(first_token),
        json={"repository_url": "https://github.com/octocat/public-repo.git"},
    )
    assert first.status_code == 200
    repository_id = first.json()["repository"]["id"]
    assert first.json()["repository"]["installation_id"] is None
    assert first.json()["repository"]["access_mode"] == "public"
    assert len(queue.enqueued) == 1

    async def second_user() -> tuple[str, uuid.UUID]:
        engine = create_async_engine(_database_url())
        try:
            async with AsyncSession(engine) as session:
                user = User(display_name="Second", email="second@example.test")
                session.add(user)
                await session.flush()
                user_id = user.id
                session.add(
                    AuthIdentity(
                        user_id=user.id,
                        provider=AuthProvider.GOOGLE,
                        provider_subject="google-subject-2",
                        provider_email="second@example.test",
                        email_verified=True,
                    )
                )
                await session.commit()
                return tokens.issue_access_token(user_id).value, user_id
        finally:
            await engine.dispose()

    second_token, second_user_id = asyncio.run(second_user())
    second = client.post(
        "/api/v1/public-repositories/import",
        headers=_authorization(second_token),
        json={"repository_url": "https://github.com/octocat/public-repo"},
    )
    assert second.status_code == 200
    assert second.json()["repository"]["id"] == repository_id
    assert len(queue.enqueued) == 1
    assert first_user_id != second_user_id
    assert asyncio.run(_scalar(select(func.count()).select_from(Repository))) == 1
    assert asyncio.run(_scalar(select(func.count()).select_from(UserRepository))) == 2

    google.identity = GoogleIdentity(
        subject="google-subject-3",
        email="third@example.test",
        email_verified=True,
    )
    third_token, _ = _google_login(client)
    denied = client.get(
        f"/api/v1/repositories/{repository_id}", headers=_authorization(third_token)
    )
    assert denied.status_code == 404


def test_public_refresh_reuses_current_commit_and_revokes_private_transition(
    application_client: tuple[
        TestClient, PublicFakeGitHub, FakeGoogleClient, FakeJobQueue, TokenService
    ],
) -> None:
    client, github, _, queue, _ = application_client
    token, _ = _google_login(client)
    imported = client.post(
        "/api/v1/public-repositories/import",
        headers=_authorization(token),
        json={"repository_url": "https://github.com/octocat/public-repo"},
    )
    repository_id = uuid.UUID(imported.json()["repository"]["id"])

    async def activate() -> None:
        engine = create_async_engine(_database_url())
        async with AsyncSession(engine) as session:
            await session.execute(
                update(IndexingJob)
                .where(IndexingJob.repository_id == repository_id)
                .values(status=IndexingJobStatus.COMPLETE)
            )
            await session.execute(
                update(Repository)
                .where(Repository.id == repository_id)
                .values(
                    index_version=1,
                    last_indexed_commit_sha=github.public_sha,
                    indexed_branch="main",
                    indexing_status=RepositoryIndexingStatus.COMPLETE,
                )
            )
            session.add(
                RepositoryIndexBuild(
                    repository_id=repository_id,
                    index_version=1,
                    state=IndexBuildState.ACTIVE,
                    cleanup_status=IndexCleanupStatus.NOT_REQUIRED,
                    commit_sha=github.public_sha,
                    embedding_model_identifier="jinaai/jina-embeddings-v2-base-code",
                    embedding_model_revision="516f4baf13dec4ddddda8631e019b5737c8bc250",
                    embedding_dimension=768,
                    preprocessing_fingerprint="f" * 64,
                    expected_chunk_count=0,
                    embedded_chunk_count=0,
                    vector_count=0,
                )
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(activate())
    unchanged = client.post(
        f"/api/v1/public-repositories/{repository_id}/refresh",
        headers=_authorization(token),
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["already_current"] is True
    assert len(queue.enqueued) == 1

    github.public_sha = "b" * 40
    changed = client.post(
        f"/api/v1/public-repositories/{repository_id}/refresh",
        headers=_authorization(token),
    )
    assert changed.status_code == 200
    assert changed.json()["already_current"] is False
    assert len(queue.enqueued) == 2

    github.public_private = True
    asyncio.run(_expire_visibility(repository_id))
    denied = client.get(f"/api/v1/repositories/{repository_id}", headers=_authorization(token))
    assert denied.status_code == 404
    revoked_at = asyncio.run(_scalar(select(Repository.access_revoked_at)))
    assert revoked_at is not None


async def _expire_visibility(repository_id: uuid.UUID) -> None:
    engine = create_async_engine(_database_url())
    async with AsyncSession(engine) as session:
        await session.execute(
            update(Repository)
            .where(Repository.id == repository_id)
            .values(visibility_checked_at=datetime.now(UTC) - timedelta(days=1))
        )
        await session.commit()
    await engine.dispose()
