"""Mock-transport verification for fixed GitHub App API operations."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError

from app.github.client import GITHUB_API_VERSION, GitHubAPIError, GitHubClient
from app.github.schemas import GitHubRepository
from tests.conftest import make_settings


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "--upload-pack=evil"),
        ("full_name", "attacker/other"),
        ("html_url", "https://evil.example/octocat/repo"),
        ("default_branch", "refs/../escape"),
    ],
)
def test_github_repository_identity_fails_closed(field: str, value: str) -> None:
    payload = {
        "id": 100,
        "owner": {"login": "octocat"},
        "name": "repo",
        "full_name": "octocat/repo",
        "html_url": "https://github.com/octocat/repo",
        "private": True,
        "default_branch": "main",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        GitHubRepository.model_validate(payload)


@pytest.mark.asyncio
async def test_github_client_uses_fixed_hosts_and_ephemeral_bearer_tokens() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(
                200, json={"access_token": "github-user-token", "token_type": "bearer"}
            )
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 1, "login": "octocat"})
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "installations": [
                        {
                            "id": 10,
                            "account": {"id": 1, "login": "octocat", "type": "User"},
                            "permissions": {"contents": "read"},
                            "repository_selection": "selected",
                            "suspended_at": None,
                        }
                    ]
                },
            )
        if request.url.path == "/app/installations/10/access_tokens":
            return httpx.Response(
                201,
                json={
                    "token": "github-installation-token",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        if request.url.path == "/installation/repositories":
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "id": 100,
                            "owner": {"login": "octocat"},
                            "name": "hello-world",
                            "full_name": "octocat/hello-world",
                            "html_url": "https://github.com/octocat/hello-world",
                            "private": True,
                            "default_branch": "main",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GitHubClient(make_settings(github_app_private_key=_private_key()), http_client)

    authorization_url = client.authorization_url(state="state", code_challenge="challenge")
    user_token = await client.exchange_code(code="code", code_verifier="verifier")
    user = await client.get_authenticated_user(user_token)
    installations = await client.list_user_installations(user_token)
    installation_token = await client.create_installation_token(10)
    repositories = await client.list_installation_repositories(installation_token)

    assert authorization_url.startswith("https://github.com/login/oauth/authorize?")
    assert "code_challenge_method=S256" in authorization_url
    assert user.login == "octocat"
    assert installations[0].id == 10
    assert repositories[0].full_name == "octocat/hello-world"
    assert all(
        request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION for request in requests
    )
    token_request = next(
        request for request in requests if request.url.path == "/app/installations/10/access_tokens"
    )
    assert token_request.url.host == "api.github.com"
    assert token_request.headers["Authorization"].startswith("Bearer ey")
    assert json.loads(token_request.content) == {
        "permissions": {"contents": "read", "metadata": "read", "pull_requests": "read"}
    }
    await http_client.aclose()


@pytest.mark.asyncio
async def test_github_client_maps_http_and_schema_failures_to_safe_error() -> None:
    def invalid_schema_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={})

    settings = make_settings(github_app_private_key=_private_key())
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(invalid_schema_handler))
    client = GitHubClient(settings, http_client)
    opaque_token = SecretStr("ephemeral-test-token")

    with pytest.raises(GitHubAPIError):
        await client.exchange_code(code="code", code_verifier="verifier")
    with pytest.raises(GitHubAPIError):
        await client.get_authenticated_user(opaque_token)
    with pytest.raises(GitHubAPIError):
        await client.list_user_installations(opaque_token)
    with pytest.raises(GitHubAPIError):
        await client.create_installation_token(10)
    with pytest.raises(GitHubAPIError):
        await client.list_installation_repositories(opaque_token)
    await http_client.aclose()

    def failure_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500)

    failed_http_client = httpx.AsyncClient(transport=httpx.MockTransport(failure_handler))
    failed_client = GitHubClient(settings, failed_http_client)
    with pytest.raises(GitHubAPIError):
        await failed_client.get_authenticated_user(opaque_token)
    await failed_http_client.aclose()

    owned_client = GitHubClient(settings)
    await owned_client.close()


@pytest.mark.asyncio
async def test_repository_history_uses_fixed_paths_and_repository_scoped_token() -> None:
    requests: list[httpx.Request] = []
    sha = "a" * 40

    def commit_payload(*, files: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "sha": sha,
            "html_url": f"https://github.com/owner/repo/commit/{sha}",
            "commit": {
                "message": "Add validation",
                "author": {"name": "Dev", "date": "2026-01-01T00:00:00Z"},
                "committer": {"name": "Dev", "date": "2026-01-01T00:00:00Z"},
            },
            "parents": [{"sha": "b" * 40}],
        }
        if files:
            result["files"] = [
                {
                    "filename": "app/service.py",
                    "status": "modified",
                    "patch": "+def validate(value): return bool(value)",
                }
            ]
        return result

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/app/installations/10/access_tokens":
            return httpx.Response(
                201,
                json={
                    "token": "ephemeral-repository-token",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
        if request.url.path == "/repos/owner/repo/commits":
            return httpx.Response(200, json=[commit_payload(files=False)])
        if request.url.path == f"/repos/owner/repo/commits/{sha}":
            return httpx.Response(200, json=commit_payload(files=True))
        if request.url.path == f"/repos/owner/repo/commits/{sha}/pulls":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 7,
                        "title": "Add validation",
                        "body": "Bounded untrusted description",
                        "state": "closed",
                        "html_url": "https://github.com/owner/repo/pull/7",
                        "user": {"login": "developer"},
                        "merged_at": "2026-01-02T00:00:00Z",
                        "merge_commit_sha": "c" * 40,
                    }
                ],
            )
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GitHubClient(make_settings(github_app_private_key=_private_key()), http_client)

    token = await client.create_repository_installation_token(10, repository_id=100)
    history = await client.get_repository_history(
        token,
        owner="owner",
        repository="repo",
        revision=sha,
        limit=3,
    )

    token_request = requests[0]
    assert json.loads(token_request.content)["repository_ids"] == [100]
    assert [request.url.path for request in requests[1:]] == [
        "/repos/owner/repo/commits",
        f"/repos/owner/repo/commits/{sha}",
        f"/repos/owner/repo/commits/{sha}/pulls",
    ]
    assert dict(requests[1].url.params) == {"sha": sha, "per_page": "3", "page": "1"}
    assert history[0].commit.files[0].filename == "app/service.py"
    assert history[0].pull_requests[0].number == 7
    await http_client.aclose()


@pytest.mark.asyncio
async def test_repository_history_rejects_invalid_scope_malformed_data_and_timeout() -> None:
    settings = make_settings(github_app_private_key=_private_key())
    token = SecretStr("ephemeral")
    malformed_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[{"sha": "bad"}]))
    )
    malformed = GitHubClient(settings, malformed_http)
    with pytest.raises(GitHubAPIError):
        await malformed.get_repository_history(
            token, owner="owner", repository="repo", revision="a" * 40, limit=1
        )
    with pytest.raises(GitHubAPIError):
        await malformed.get_repository_history(
            token,
            owner="../attacker",
            repository="repo",
            revision="a" * 40,
            limit=1,
        )
    await malformed_http.aclose()

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test timeout", request=request)

    timeout_http = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    timed_out = GitHubClient(settings, timeout_http)
    with pytest.raises(GitHubAPIError):
        await timed_out.get_repository_history(
            token, owner="owner", repository="repo", revision="a" * 40, limit=1
        )
    await timeout_http.aclose()


@pytest.mark.asyncio
async def test_repository_history_retries_rate_limit_but_not_authentication_failure() -> None:
    settings = make_settings(github_app_private_key=_private_key())
    token = SecretStr("ephemeral")
    rate_attempts = 0

    def rate_handler(request: httpx.Request) -> httpx.Response:
        nonlocal rate_attempts
        rate_attempts += 1
        return httpx.Response(429, request=request)

    rate_http = httpx.AsyncClient(transport=httpx.MockTransport(rate_handler))
    rate_client = GitHubClient(settings, rate_http)
    with pytest.raises(GitHubAPIError):
        await rate_client.get_repository_history(
            token, owner="owner", repository="repo", revision="a" * 40, limit=1
        )
    assert rate_attempts == 2
    await rate_http.aclose()

    auth_attempts = 0

    def auth_handler(request: httpx.Request) -> httpx.Response:
        nonlocal auth_attempts
        auth_attempts += 1
        return httpx.Response(401, request=request)

    auth_http = httpx.AsyncClient(transport=httpx.MockTransport(auth_handler))
    auth_client = GitHubClient(settings, auth_http)
    with pytest.raises(GitHubAPIError):
        await auth_client.get_repository_history(
            token, owner="owner", repository="repo", revision="a" * 40, limit=1
        )
    assert auth_attempts == 1
    await auth_http.aclose()


@pytest.mark.asyncio
async def test_repository_compare_uses_fixed_scope_and_rejects_unsafe_paths() -> None:
    base = "a" * 40
    head = "b" * 40
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "ahead",
                "ahead_by": 1,
                "behind_by": 0,
                "total_commits": 1,
                "files": [{"filename": "src/app.py", "status": "modified", "changes": 4}],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GitHubClient(make_settings(), http_client)
    result = await client.compare_repository_commits(
        SecretStr("ephemeral-repository-token"),
        owner="owner",
        repository="repo",
        base=base,
        head=head,
    )
    assert result.files[0].filename == "src/app.py"
    assert requests[0].url.path == f"/repos/owner/repo/compare/{base}...{head}"
    assert requests[0].headers["Authorization"] == "Bearer ephemeral-repository-token"

    with pytest.raises(GitHubAPIError):
        await client.compare_repository_commits(
            SecretStr("ephemeral"),
            owner="../owner",
            repository="repo",
            base=base,
            head=head,
        )
    await http_client.aclose()
