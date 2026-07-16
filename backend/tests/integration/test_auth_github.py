"""PostgreSQL-backed authentication, authorization, and webhook workflows."""

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.sql import Executable

from app.application import create_app
from app.auth.cookies import AUTH_COOKIE_PATH, PKCE_COOKIE_NAME
from app.auth.tokens import TokenService
from app.db.models.auth import OAuthState, RefreshToken
from app.db.models.enums import (
    InstallationStatus,
    RepositoryIndexingStatus,
    WebhookDeliveryStatus,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.repository import Repository
from app.db.models.user import User
from app.db.models.webhook_delivery import WebhookDelivery
from app.db.session import Database
from app.github.client import GitHubAPIError
from app.github.schemas import GitHubInstallation as GitHubInstallationData
from app.github.schemas import GitHubRepository, GitHubUser
from app.services.auth import AuthService, OAuthStateError, RefreshTokenError
from app.services.installations import InstallationAccessError, InstallationService
from app.services.webhooks import WebhookService
from tests.conftest import make_settings

pytestmark = pytest.mark.integration

GITHUB_USER_TOKEN = "github-user-token-sensitive-sentinel"
GITHUB_INSTALLATION_TOKEN = "github-installation-token-sensitive-sentinel"
OAUTH_CODE = "oauth-code-sensitive-sentinel"


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
                "TRUNCATE TABLE call_edges, chat_messages, chat_sessions, indexing_jobs, "
                "oauth_states, refresh_tokens, repositories, symbol_definitions, usage_records, "
                "installation_members, webhook_deliveries, github_installations, users CASCADE"
            )
        )
    await engine.dispose()


async def _database_scalar(statement: Executable) -> object:
    engine = create_async_engine(_database_url())
    async with AsyncSession(engine) as session:
        value = await session.scalar(statement)
    await engine.dispose()
    return value


class FakeGitHubClient:
    """No-network GitHub contract used by every integration test."""

    def __init__(self) -> None:
        self.user = GitHubUser(
            id=101,
            login="octocat",
            name="Octo Cat",
            avatar_url="https://avatars.githubusercontent.com/u/101",
            email="octocat@example.test",
        )
        self.installations: Sequence[GitHubInstallationData] = (
            GitHubInstallationData.model_validate(
                {
                    "id": 501,
                    "account": {"id": 101, "login": "octocat", "type": "User"},
                    "permissions": {
                        "contents": "read",
                        "metadata": "read",
                        "pull_requests": "read",
                    },
                    "repository_selection": "selected",
                    "suspended_at": None,
                }
            ),
        )
        self.repositories: Sequence[GitHubRepository] = (
            GitHubRepository.model_validate(
                {
                    "id": 9001,
                    "owner": {"login": "octocat"},
                    "name": "private-repo",
                    "full_name": "octocat/private-repo",
                    "html_url": "https://github.com/octocat/private-repo",
                    "private": True,
                    "default_branch": "main",
                    "language": "Python",
                }
            ),
        )
        self.exchanged_codes: list[str] = []
        self.installation_token_requests: list[int] = []
        self.fail_exchange = False
        self.fail_installation_token = False
        self.closed = False

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        return (
            "https://github.com/login/oauth/authorize"
            f"?client_id=test-client-id&state={state}&code_challenge={code_challenge}"
            "&code_challenge_method=S256"
        )

    async def exchange_code(self, *, code: str, code_verifier: str) -> SecretStr:
        assert len(code_verifier) >= 43
        if self.fail_exchange:
            raise GitHubAPIError
        self.exchanged_codes.append(code)
        return SecretStr(GITHUB_USER_TOKEN)

    async def get_authenticated_user(self, access_token: SecretStr) -> GitHubUser:
        assert access_token.get_secret_value() == GITHUB_USER_TOKEN
        return self.user

    async def list_user_installations(
        self,
        access_token: SecretStr,
    ) -> Sequence[GitHubInstallationData]:
        assert access_token.get_secret_value() == GITHUB_USER_TOKEN
        return self.installations

    async def create_installation_token(self, installation_id: int) -> SecretStr:
        if self.fail_installation_token:
            raise GitHubAPIError
        self.installation_token_requests.append(installation_id)
        return SecretStr(GITHUB_INSTALLATION_TOKEN)

    async def list_installation_repositories(
        self,
        installation_token: SecretStr,
    ) -> Sequence[GitHubRepository]:
        assert installation_token.get_secret_value() == GITHUB_INSTALLATION_TOKEN
        return self.repositories

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    asyncio.run(_reset_database())
    yield
    asyncio.run(_reset_database())


@pytest.fixture
def github() -> FakeGitHubClient:
    return FakeGitHubClient()


@pytest.fixture
def auth_client(github: FakeGitHubClient) -> Iterator[TestClient]:
    settings = make_settings()
    database = Database(
        engine=create_async_engine(_database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(settings=settings, database=database, github_client=github)
    with TestClient(app) as client:
        yield client


def _start_oauth(client: TestClient) -> tuple[str, str]:
    response = client.get("/api/v1/auth/github/start", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    verifier = client.cookies.get(PKCE_COOKIE_NAME, path=AUTH_COOKIE_PATH)
    assert verifier is not None
    assert "HttpOnly" in response.headers["set-cookie"]
    return state, verifier


def _login(client: TestClient) -> tuple[str, str, str]:
    state, verifier = _start_oauth(client)
    response = client.get(
        "/api/v1/auth/github/callback",
        params={"code": OAUTH_CODE, "state": state},
    )
    assert response.status_code == 200
    access_token = response.json()["access_token"]
    refresh_token = client.cookies.get("repolume_refresh_token")
    assert refresh_token is not None
    assert "HttpOnly" in response.headers["set-cookie"]
    assert GITHUB_USER_TOKEN not in response.text
    return access_token, refresh_token, verifier


def _authorization(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _webhook_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _installation_payload(action: str, *, suspended: bool = False) -> bytes:
    return json.dumps(
        {
            "action": action,
            "installation": {
                "id": 501,
                "account": {"id": 101, "login": "octocat", "type": "User"},
                "permissions": {
                    "contents": "read",
                    "metadata": "read",
                    "pull_requests": "read",
                },
                "repository_selection": "selected",
                "suspended_at": datetime.now(UTC).isoformat() if suspended else None,
            },
            "sender": {"id": 101, "login": "octocat"},
        }
    ).encode()


def _post_webhook(
    client: TestClient,
    body: bytes,
    *,
    delivery_id: str,
    event: str,
    valid_signature: bool = True,
) -> Any:
    secret = make_settings().github_webhook_secret.get_secret_value()
    signature = _webhook_signature(secret, body) if valid_signature else "sha256=invalid"
    return client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature,
        },
    )


def test_github_login_me_installations_and_repository_listing(
    auth_client: TestClient,
    github: FakeGitHubClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = auth_client.get("/api/v1/auth/me")
    tampered = auth_client.get(
        "/api/v1/auth/me",
        headers=_authorization("not-a-valid-access-token"),
    )
    assert missing.status_code == 401
    assert tampered.status_code == 401

    access_token, refresh_token, _ = _login(auth_client)

    me = auth_client.get("/api/v1/auth/me", headers=_authorization(access_token))
    installations = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    )
    installation_id = installations.json()[0]["id"]
    repositories = auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(access_token),
    )

    assert me.status_code == 200
    assert me.json()["github_login"] == "octocat"
    assert installations.status_code == 200
    assert installations.json()[0]["status"] == "active"
    assert repositories.status_code == 200
    assert repositories.json()[0]["github_full_name"] == "octocat/private-repo"
    assert github.installation_token_requests == [501]
    stored_hash = asyncio.run(_database_scalar(select(RefreshToken.token_hash)))
    assert isinstance(stored_hash, str)
    assert refresh_token not in stored_hash

    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert OAUTH_CODE not in logs
    assert GITHUB_USER_TOKEN not in logs
    assert GITHUB_INSTALLATION_TOKEN not in logs
    assert refresh_token not in logs
    assert access_token not in logs


def test_oauth_state_replay_mismatch_and_expiry_are_rejected(
    auth_client: TestClient,
    github: FakeGitHubClient,
) -> None:
    state, verifier = _start_oauth(auth_client)
    mismatch = auth_client.get(
        "/api/v1/auth/github/callback",
        params={"code": OAUTH_CODE, "state": state + "wrong"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "oauth_state_invalid"

    success = auth_client.get(
        "/api/v1/auth/github/callback",
        params={"code": OAUTH_CODE, "state": state},
    )
    assert success.status_code == 200
    auth_client.cookies.set(PKCE_COOKIE_NAME, verifier, path=AUTH_COOKIE_PATH)
    replay = auth_client.get(
        "/api/v1/auth/github/callback",
        params={"code": OAUTH_CODE, "state": state},
    )
    assert replay.status_code == 400
    assert github.exchanged_codes == [OAUTH_CODE]

    auth_client.cookies.clear()
    expiring_state, _ = _start_oauth(auth_client)

    async def expire_state() -> None:
        engine = create_async_engine(_database_url())
        async with AsyncSession(engine) as session:
            await session.execute(
                update(OAuthState)
                .where(OAuthState.used_at.is_(None))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(expire_state())
    expired = auth_client.get(
        "/api/v1/auth/github/callback",
        params={"code": OAUTH_CODE, "state": expiring_state},
    )
    assert expired.status_code == 400


def test_refresh_rotation_reuse_detection_logout_and_origin_enforcement(
    auth_client: TestClient,
) -> None:
    _, first_refresh, _ = _login(auth_client)

    blocked = auth_client.post("/api/v1/auth/refresh")
    assert blocked.status_code == 403

    rotated = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://testserver"},
    )
    assert rotated.status_code == 200
    second_refresh = auth_client.cookies.get("repolume_refresh_token")
    assert second_refresh is not None
    assert second_refresh != first_refresh

    auth_client.cookies.set("repolume_refresh_token", first_refresh, path=AUTH_COOKIE_PATH)
    reuse = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://testserver"},
    )
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "token_reuse_detected"

    auth_client.cookies.set("repolume_refresh_token", second_refresh, path=AUTH_COOKIE_PATH)
    family_invalidated = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://testserver"},
    )
    assert family_invalidated.status_code == 401
    revoked_count = asyncio.run(
        _database_scalar(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.revoked_at.is_not(None))
        )
    )
    assert revoked_count == 2

    logout = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver"},
    )
    assert logout.status_code == 204


def test_authentication_dependency_and_token_failures_are_safe(
    auth_client: TestClient,
    github: FakeGitHubClient,
) -> None:
    state, _ = _start_oauth(auth_client)
    github.fail_exchange = True
    unavailable = auth_client.get(
        "/api/v1/auth/github/callback",
        params={"code": OAUTH_CODE, "state": state},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "service_unavailable"

    github.fail_exchange = False
    auth_client.cookies.clear()
    missing = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://testserver"},
    )
    assert missing.status_code == 401

    auth_client.cookies.set("repolume_refresh_token", "unknown-refresh-token")
    unknown = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://testserver"},
    )
    assert unknown.status_code == 401

    auth_client.cookies.clear()
    _, raw_refresh, _ = _login(auth_client)

    async def expire_refresh_token() -> None:
        engine = create_async_engine(_database_url())
        token_hash = TokenService(make_settings()).hash_opaque_token(raw_refresh)
        async with AsyncSession(engine) as session:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.token_hash == token_hash)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(expire_refresh_token())
    expired = auth_client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://testserver"},
    )
    assert expired.status_code == 401
    auth_client.cookies.clear()
    logout_without_cookie = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver"},
    )
    assert logout_without_cookie.status_code == 204


def test_reauthentication_updates_user_and_removes_stale_memberships(
    auth_client: TestClient,
    github: FakeGitHubClient,
) -> None:
    _, first_refresh, _ = _login(auth_client)
    github.user = GitHubUser(
        id=101,
        login="renamed-octocat",
        name="Renamed User",
        avatar_url="https://avatars.githubusercontent.com/u/101?v=2",
        email=None,
    )
    github.installations = ()
    auth_client.cookies.clear()
    second_access, _, _ = _login(auth_client)

    me = auth_client.get("/api/v1/auth/me", headers=_authorization(second_access))
    assert me.json()["github_login"] == "renamed-octocat"
    assert me.json()["display_name"] == "Renamed User"
    assert (
        auth_client.get(
            "/api/v1/installations",
            headers=_authorization(second_access),
        ).json()
        == []
    )
    user_count = asyncio.run(_database_scalar(select(func.count()).select_from(User)))
    membership_count = asyncio.run(
        _database_scalar(select(func.count()).select_from(InstallationMember))
    )
    stored_hash = asyncio.run(_database_scalar(select(RefreshToken.token_hash)))
    assert user_count == 1
    assert membership_count == 0
    assert first_refresh not in repr(stored_hash)


def test_organization_installation_membership_and_suspension_sync(
    auth_client: TestClient,
    github: FakeGitHubClient,
) -> None:
    github.installations = (
        GitHubInstallationData.model_validate(
            {
                "id": 777,
                "account": {"id": 700, "login": "example-org", "type": "Organization"},
                "permissions": {
                    "contents": "read",
                    "metadata": "read",
                    "pull_requests": "read",
                },
                "repository_selection": "all",
                "suspended_at": datetime.now(UTC).isoformat(),
            }
        ),
    )
    access_token, _, _ = _login(auth_client)

    assert (
        auth_client.get(
            "/api/v1/installations",
            headers=_authorization(access_token),
        ).json()
        == []
    )
    status_value = asyncio.run(
        _database_scalar(
            select(GitHubInstallation.status).where(
                GitHubInstallation.github_installation_id == 777
            )
        )
    )
    role = asyncio.run(_database_scalar(select(InstallationMember.role)))
    assert status_value == InstallationStatus.SUSPENDED
    assert str(role) == "member"


def test_cross_user_installation_and_repository_access_is_denied(
    auth_client: TestClient,
    github: FakeGitHubClient,
) -> None:
    first_access, _, _ = _login(auth_client)
    installations = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(first_access),
    ).json()
    installation_id = installations[0]["id"]
    repositories = auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(first_access),
    ).json()
    repository_id = uuid.UUID(repositories[0]["id"])

    async def create_other_user() -> tuple[uuid.UUID, str]:
        engine = create_async_engine(_database_url())
        database = Database(engine=engine, ready_timeout_seconds=2)
        async with database.session() as session:
            user = User(github_user_id=202, github_login="other-user")
            session.add(user)
            await session.commit()
        token = TokenService(make_settings()).issue_access_token(user.id).value
        await database.dispose()
        return user.id, token

    other_user_id, other_access = asyncio.run(create_other_user())
    request_count = len(github.installation_token_requests)
    denied = auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(other_access),
    )
    assert denied.status_code == 404
    assert len(github.installation_token_requests) == request_count

    async def check_repository_denial() -> None:
        database = Database(
            engine=create_async_engine(_database_url()),
            ready_timeout_seconds=2,
        )
        service = InstallationService(database, github, make_settings())
        with pytest.raises(InstallationAccessError):
            await service.get_authorized_repository(
                user_id=other_user_id,
                repository_id=repository_id,
            )
        await database.dispose()

    asyncio.run(check_repository_denial())

    async def expire_membership() -> None:
        engine = create_async_engine(_database_url())
        async with AsyncSession(engine) as session:
            await session.execute(
                update(InstallationMember)
                .where(InstallationMember.user_id != other_user_id)
                .values(verified_at=datetime.now(UTC) - timedelta(days=2))
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(expire_membership())
    stale = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(first_access),
    )
    assert stale.status_code == 200
    assert stale.json() == []


def test_installation_created_webhook_restores_known_sender_membership(
    auth_client: TestClient,
) -> None:
    access_token, _, _ = _login(auth_client)

    async def remove_membership() -> None:
        engine = create_async_engine(_database_url())
        async with AsyncSession(engine) as session:
            await session.execute(delete(InstallationMember))
            await session.commit()
        await engine.dispose()

    asyncio.run(remove_membership())
    hidden = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    )
    assert hidden.json() == []

    created = _post_webhook(
        auth_client,
        _installation_payload("created"),
        delivery_id="delivery-created",
        event="installation",
    )
    assert created.status_code == 202
    restored = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    )
    assert restored.json()[0]["status"] == "active"


def test_webhook_signature_idempotency_suspension_unsuspension_and_removal(
    auth_client: TestClient,
) -> None:
    access_token, _, _ = _login(auth_client)
    installations = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    ).json()
    installation_id = installations[0]["id"]
    auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(access_token),
    )

    suspended_body = _installation_payload("suspend", suspended=True)
    invalid = _post_webhook(
        auth_client,
        suspended_body,
        delivery_id="delivery-invalid",
        event="installation",
        valid_signature=False,
    )
    assert invalid.status_code == 401
    malformed = _post_webhook(
        auth_client,
        b"{",
        delivery_id="delivery-malformed",
        event="installation",
    )
    assert malformed.status_code == 400
    missing_installation = _post_webhook(
        auth_client,
        b"{}",
        delivery_id="delivery-missing-installation",
        event="installation",
    )
    assert missing_installation.status_code == 400

    suspended = _post_webhook(
        auth_client,
        suspended_body,
        delivery_id="delivery-suspend",
        event="installation",
    )
    duplicate = _post_webhook(
        auth_client,
        suspended_body,
        delivery_id="delivery-suspend",
        event="installation",
    )
    assert suspended.status_code == 202
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "duplicate"}
    hidden = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    )
    assert hidden.json() == []

    repository_status = asyncio.run(
        _database_scalar(
            select(Repository.indexing_status).where(Repository.github_repository_id == 9001)
        )
    )
    assert repository_status == RepositoryIndexingStatus.ACCESS_REVOKED

    unsuspended = _post_webhook(
        auth_client,
        _installation_payload("unsuspend"),
        delivery_id="delivery-unsuspend",
        event="installation",
    )
    assert unsuspended.status_code == 202
    visible = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    )
    assert visible.json()[0]["status"] == "active"

    removed_body = json.dumps(
        {
            "action": "removed",
            "installation": json.loads(_installation_payload("created"))["installation"],
            "repositories_removed": [{"id": 9001}],
        }
    ).encode()
    removed = _post_webhook(
        auth_client,
        removed_body,
        delivery_id="delivery-remove",
        event="installation_repositories",
    )
    assert removed.status_code == 202
    revoked_at = asyncio.run(
        _database_scalar(
            select(Repository.access_revoked_at).where(Repository.github_repository_id == 9001)
        )
    )
    assert isinstance(revoked_at, datetime)

    deleted = _post_webhook(
        auth_client,
        _installation_payload("deleted"),
        delivery_id="delivery-delete",
        event="installation",
    )
    assert deleted.status_code == 202
    installation_status = asyncio.run(
        _database_scalar(
            select(GitHubInstallation.status).where(
                GitHubInstallation.github_installation_id == 501
            )
        )
    )
    assert installation_status == InstallationStatus.DELETED
    delivery_count = asyncio.run(
        _database_scalar(select(func.count()).select_from(WebhookDelivery))
    )
    assert delivery_count == 4


def test_repository_access_addition_deletion_and_ignored_webhook_paths(
    auth_client: TestClient,
) -> None:
    access_token, _, _ = _login(auth_client)
    installation_id = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    ).json()[0]["id"]

    missing_signature = auth_client.post(
        "/api/v1/webhooks/github",
        content=b"{}",
        headers={
            "X-GitHub-Delivery": "delivery-no-signature",
            "X-GitHub-Event": "push",
        },
    )
    invalid_header = _post_webhook(
        auth_client,
        b"{}",
        delivery_id="delivery with spaces",
        event="push",
    )
    oversized = auth_client.post(
        "/api/v1/webhooks/github",
        content=b"x" * 1_048_577,
    )
    assert missing_signature.status_code == 401
    assert invalid_header.status_code == 400
    assert oversized.status_code == 413

    ignored = _post_webhook(
        auth_client,
        b"{}",
        delivery_id="delivery-ignored",
        event="issues",
    )
    assert ignored.status_code == 200
    assert ignored.json() == {"status": "ignored"}

    added_repository = {
        "id": 9002,
        "owner": {"login": "octocat"},
        "name": "added-repo",
        "full_name": "octocat/added-repo",
        "html_url": "https://github.com/octocat/added-repo",
        "private": True,
        "default_branch": "main",
        "language": "Python",
    }
    installation_payload = json.loads(_installation_payload("created"))["installation"]
    added_body = json.dumps(
        {
            "action": "added",
            "installation": installation_payload,
            "repositories_added": [added_repository],
        }
    ).encode()
    added = _post_webhook(
        auth_client,
        added_body,
        delivery_id="delivery-added",
        event="installation_repositories",
    )
    assert added.status_code == 202
    added_id = asyncio.run(
        _database_scalar(select(Repository.id).where(Repository.github_repository_id == 9002))
    )
    assert isinstance(added_id, uuid.UUID)

    deleted_body = json.dumps(
        {
            "action": "deleted",
            "installation": installation_payload,
            "repository": added_repository,
        }
    ).encode()
    deleted = _post_webhook(
        auth_client,
        deleted_body,
        delivery_id="delivery-repository-delete",
        event="repository",
    )
    assert deleted.status_code == 202
    deleted_at = asyncio.run(
        _database_scalar(select(Repository.deleted_at).where(Repository.id == added_id))
    )
    assert isinstance(deleted_at, datetime)

    queued_repository = _post_webhook(
        auth_client,
        json.dumps(
            {
                "action": "renamed",
                "installation": installation_payload,
                "repository": added_repository,
            }
        ).encode(),
        delivery_id="delivery-repository-renamed",
        event="repository",
    )
    assert queued_repository.status_code == 202
    queue_status = asyncio.run(
        _database_scalar(
            select(WebhookDelivery.status).where(
                WebhookDelivery.delivery_id == "delivery-repository-renamed"
            )
        )
    )
    assert queue_status == WebhookDeliveryStatus.QUEUED

    assert (
        auth_client.get(
            f"/api/v1/installations/{installation_id}/repositories",
            headers=_authorization(access_token),
        ).status_code
        == 200
    )


def test_repository_listing_github_failure_and_empty_sync_revoke_access(
    auth_client: TestClient,
    github: FakeGitHubClient,
) -> None:
    access_token, _, _ = _login(auth_client)
    installation_id = auth_client.get(
        "/api/v1/installations",
        headers=_authorization(access_token),
    ).json()[0]["id"]
    first = auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(access_token),
    )
    assert first.status_code == 200

    github.fail_installation_token = True
    unavailable = auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(access_token),
    )
    assert unavailable.status_code == 503
    github.fail_installation_token = False
    github.repositories = ()
    empty = auth_client.get(
        f"/api/v1/installations/{installation_id}/repositories",
        headers=_authorization(access_token),
    )
    assert empty.status_code == 200
    assert empty.json() == []
    revoked = asyncio.run(
        _database_scalar(
            select(Repository.indexing_status).where(Repository.github_repository_id == 9001)
        )
    )
    assert revoked == RepositoryIndexingStatus.ACCESS_REVOKED


def test_push_webhook_records_durable_queued_state_without_worker(
    auth_client: TestClient,
) -> None:
    body = json.dumps(
        {
            "installation": json.loads(_installation_payload("created"))["installation"],
            "repository": {
                "id": 9001,
                "owner": {"login": "octocat"},
                "name": "private-repo",
                "full_name": "octocat/private-repo",
                "html_url": "https://github.com/octocat/private-repo",
                "private": True,
                "default_branch": "main",
            },
        }
    ).encode()
    response = _post_webhook(
        auth_client,
        body,
        delivery_id="delivery-push",
        event="push",
    )

    assert response.status_code == 202
    delivery_status = asyncio.run(
        _database_scalar(
            select(WebhookDelivery.status).where(WebhookDelivery.delivery_id == "delivery-push")
        )
    )
    assert delivery_status == WebhookDeliveryStatus.QUEUED


@pytest.mark.asyncio
async def test_auth_service_direct_persistence_and_token_error_paths(
    github: FakeGitHubClient,
) -> None:
    settings = make_settings()
    database = Database(
        engine=create_async_engine(_database_url()),
        ready_timeout_seconds=2,
    )
    tokens = TokenService(settings)
    service = AuthService(database, github, tokens, settings)

    with pytest.raises(OAuthStateError):
        await service.authenticate_callback(code="code", state="state", code_verifier=None)

    start = await service.start_oauth()
    with pytest.raises(OAuthStateError):
        await service.authenticate_callback(
            code="code",
            state=start.credentials.state + "mismatch",
            code_verifier=start.credentials.code_verifier,
        )
    authenticated = await service.authenticate_callback(
        code="code",
        state=start.credentials.state,
        code_verifier=start.credentials.code_verifier,
    )
    rotated = await service.rotate_refresh_token(authenticated.refresh_token)
    await service.logout(rotated.refresh_token)
    with pytest.raises(RefreshTokenError):
        await service.rotate_refresh_token(rotated.refresh_token)
    with pytest.raises(RefreshTokenError):
        await service.rotate_refresh_token("unknown-refresh-token")
    await service.logout(None)

    github.user = GitHubUser(id=101, login="updated-login", name="Updated User")
    github.installations = ()
    second_start = await service.start_oauth()
    second = await service.authenticate_callback(
        code="second-code",
        state=second_start.credentials.state,
        code_verifier=second_start.credentials.code_verifier,
    )
    assert second.user.id == authenticated.user.id
    assert second.user.github_login == "updated-login"

    second_hash = tokens.hash_opaque_token(second.refresh_token)
    async with database.session() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == second_hash)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    with pytest.raises(RefreshTokenError):
        await service.rotate_refresh_token(second.refresh_token)

    await database.dispose()


@pytest.mark.asyncio
async def test_installation_service_direct_sync_restores_and_revokes(
    github: FakeGitHubClient,
) -> None:
    settings = make_settings()
    database = Database(
        engine=create_async_engine(_database_url()),
        ready_timeout_seconds=2,
    )
    auth_service = AuthService(database, github, TokenService(settings), settings)
    start = await auth_service.start_oauth()
    authenticated = await auth_service.authenticate_callback(
        code="code",
        state=start.credentials.state,
        code_verifier=start.credentials.code_verifier,
    )
    service = InstallationService(database, github, settings)

    installations = await service.list_authorized_installations(authenticated.user.id)
    installation = await service.get_authorized_installation(
        user_id=authenticated.user.id,
        installation_id=installations[0].id,
    )
    repositories = await service.synchronize_repositories(
        user_id=authenticated.user.id,
        installation_id=installation.id,
    )
    authorized = await service.get_authorized_repository(
        user_id=authenticated.user.id,
        repository_id=repositories[0].id,
    )
    assert authorized.github_repository_id == 9001

    github.repositories = ()
    assert (
        await service.synchronize_repositories(
            user_id=authenticated.user.id,
            installation_id=installation.id,
        )
        == ()
    )
    github.repositories = (
        GitHubRepository.model_validate(
            {
                "id": 9001,
                "owner": {"login": "renamed-owner"},
                "name": "renamed-repo",
                "full_name": "renamed-owner/renamed-repo",
                "html_url": "https://github.com/renamed-owner/renamed-repo",
                "private": True,
                "default_branch": "trunk",
                "language": "Python",
            }
        ),
    )
    restored = await service.synchronize_repositories(
        user_id=authenticated.user.id,
        installation_id=installation.id,
    )
    assert restored[0].github_full_name == "renamed-owner/renamed-repo"
    assert restored[0].indexing_status == RepositoryIndexingStatus.NOT_INDEXED

    with pytest.raises(InstallationAccessError):
        await service.get_authorized_installation(
            user_id=authenticated.user.id,
            installation_id=uuid.uuid4(),
        )
    with pytest.raises(InstallationAccessError):
        await service.get_authorized_repository(
            user_id=authenticated.user.id,
            repository_id=uuid.uuid4(),
        )

    await database.dispose()


@pytest.mark.asyncio
async def test_webhook_service_direct_durable_transitions(
    github: FakeGitHubClient,
) -> None:
    settings = make_settings()
    database = Database(
        engine=create_async_engine(_database_url()),
        ready_timeout_seconds=2,
    )
    auth_service = AuthService(database, github, TokenService(settings), settings)
    start = await auth_service.start_oauth()
    authenticated = await auth_service.authenticate_callback(
        code="code",
        state=start.credentials.state,
        code_verifier=start.credentials.code_verifier,
    )
    installation_service = InstallationService(database, github, settings)
    installations = await installation_service.list_authorized_installations(authenticated.user.id)
    await installation_service.synchronize_repositories(
        user_id=authenticated.user.id,
        installation_id=installations[0].id,
    )
    async with database.session() as session:
        await session.execute(delete(InstallationMember))
        await session.commit()

    service = WebhookService(database, settings)
    secret = settings.github_webhook_secret.get_secret_value()

    async def handle(body: bytes, delivery_id: str, event: str) -> str:
        return await service.handle(
            body=body,
            signature=_webhook_signature(secret, body),
            delivery_id=delivery_id,
            event_name=event,
        )

    created_body = _installation_payload("created")
    assert await handle(created_body, "direct-created", "installation") == "accepted"
    assert await handle(created_body, "direct-created", "installation") == "duplicate"
    assert await handle(b"{}", "direct-ignored", "issues") == "ignored"
    assert (
        await handle(
            _installation_payload("suspend", suspended=True),
            "direct-suspend",
            "installation",
        )
        == "accepted"
    )
    assert (
        await handle(
            _installation_payload("unsuspend"),
            "direct-unsuspend",
            "installation",
        )
        == "accepted"
    )

    repository = {
        "id": 9002,
        "owner": {"login": "octocat"},
        "name": "direct-repo",
        "full_name": "octocat/direct-repo",
        "html_url": "https://github.com/octocat/direct-repo",
        "private": True,
        "default_branch": "main",
    }
    installation_payload = json.loads(created_body)["installation"]
    added_body = json.dumps(
        {
            "action": "added",
            "installation": installation_payload,
            "repositories_added": [repository],
        }
    ).encode()
    assert (
        await handle(added_body, "direct-repository-added", "installation_repositories")
        == "accepted"
    )
    async with database.session() as session:
        await session.execute(
            update(Repository)
            .where(Repository.github_repository_id == 9002)
            .values(
                access_revoked_at=datetime.now(UTC),
                indexing_status=RepositoryIndexingStatus.ACCESS_REVOKED,
            )
        )
        await session.commit()
    assert (
        await handle(added_body, "direct-repository-restored", "installation_repositories")
        == "accepted"
    )

    deleted_body = json.dumps(
        {
            "action": "deleted",
            "installation": installation_payload,
            "repository": repository,
        }
    ).encode()
    assert await handle(deleted_body, "direct-repository-deleted", "repository") == "accepted"
    deleted_at = await _database_scalar(
        select(Repository.deleted_at).where(Repository.github_repository_id == 9002)
    )
    assert isinstance(deleted_at, datetime)

    await database.dispose()
