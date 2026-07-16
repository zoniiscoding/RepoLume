"""Mock-transport verification for fixed GitHub App API operations."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.github.client import GITHUB_API_VERSION, GitHubAPIError, GitHubClient
from tests.conftest import make_settings


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


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
