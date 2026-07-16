"""GitHub App client with fixed destinations and ephemeral credentials."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.github.schemas import (
    GitHubInstallation,
    GitHubInstallationToken,
    GitHubOAuthToken,
    GitHubRepository,
    GitHubUser,
)

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_VERSION = "2026-03-10"
MAX_PAGES = 10
PAGE_SIZE = 100


class GitHubAPIError(RuntimeError):
    """Safe GitHub dependency failure without response bodies or credentials."""


class GitHubClientProtocol(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> SecretStr: ...

    async def get_authenticated_user(self, access_token: SecretStr) -> GitHubUser: ...

    async def list_user_installations(
        self,
        access_token: SecretStr,
    ) -> Sequence[GitHubInstallation]: ...

    async def create_installation_token(self, installation_id: int) -> SecretStr: ...

    async def list_installation_repositories(
        self,
        installation_token: SecretStr,
    ) -> Sequence[GitHubRepository]: ...

    async def close(self) -> None: ...


class GitHubClient:
    """Perform only approved GitHub App OAuth and installation operations."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
        )
        self._owns_http_client = http_client is None

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        """Build the fixed GitHub authorization URL with state and S256 PKCE."""
        query = urlencode(
            {
                "client_id": self._settings.github_client_id,
                "redirect_uri": str(self._settings.github_oauth_callback_url),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{GITHUB_AUTHORIZE_URL}?{query}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> SecretStr:
        """Exchange a one-time authorization code without logging either secret."""
        response = await self._request(
            "POST",
            GITHUB_TOKEN_URL,
            data={
                "client_id": self._settings.github_client_id,
                "client_secret": self._settings.github_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": str(self._settings.github_oauth_callback_url),
                "code_verifier": code_verifier,
            },
        )
        try:
            return GitHubOAuthToken.model_validate(response.json()).access_token
        except (ValueError, ValidationError) as error:
            raise GitHubAPIError from error

    async def get_authenticated_user(self, access_token: SecretStr) -> GitHubUser:
        response = await self._request(
            "GET",
            f"{GITHUB_API_ROOT}/user",
            token=access_token,
        )
        try:
            return GitHubUser.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise GitHubAPIError from error

    async def list_user_installations(
        self,
        access_token: SecretStr,
    ) -> Sequence[GitHubInstallation]:
        items: list[GitHubInstallation] = []
        for page in range(1, MAX_PAGES + 1):
            response = await self._request(
                "GET",
                f"{GITHUB_API_ROOT}/user/installations",
                token=access_token,
                params={"per_page": PAGE_SIZE, "page": page},
            )
            try:
                raw_items = response.json()["installations"]
                page_items = [GitHubInstallation.model_validate(item) for item in raw_items]
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise GitHubAPIError from error
            items.extend(page_items)
            if len(page_items) < PAGE_SIZE:
                break
        return items

    async def create_installation_token(self, installation_id: int) -> SecretStr:
        """Mint a one-hour, read-only installation token and keep it in memory only."""
        app_token = self._create_app_jwt()
        response = await self._request(
            "POST",
            f"{GITHUB_API_ROOT}/app/installations/{installation_id}/access_tokens",
            token=SecretStr(app_token),
            json={
                "permissions": {
                    "contents": "read",
                    "metadata": "read",
                    "pull_requests": "read",
                }
            },
        )
        try:
            return GitHubInstallationToken.model_validate(response.json()).token
        except (ValueError, ValidationError) as error:
            raise GitHubAPIError from error

    async def list_installation_repositories(
        self,
        installation_token: SecretStr,
    ) -> Sequence[GitHubRepository]:
        items: list[GitHubRepository] = []
        for page in range(1, MAX_PAGES + 1):
            response = await self._request(
                "GET",
                f"{GITHUB_API_ROOT}/installation/repositories",
                token=installation_token,
                params={"per_page": PAGE_SIZE, "page": page},
            )
            try:
                raw_items = response.json()["repositories"]
                page_items = [GitHubRepository.model_validate(item) for item in raw_items]
            except (KeyError, TypeError, ValueError, ValidationError) as error:
                raise GitHubAPIError from error
            items.extend(page_items)
            if len(page_items) < PAGE_SIZE:
                break
        return items

    def _create_app_jwt(self) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iat": now - timedelta(seconds=60),
                "exp": now + timedelta(minutes=9),
                "iss": str(self._settings.github_app_id),
            },
            self._settings.github_app_private_key.get_secret_value(),
            algorithm="RS256",
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        token: SecretStr | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "RepoLume/0.2",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        try:
            response = await self._http.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GitHubAPIError from error
        return response

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()
