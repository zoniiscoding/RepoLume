"""GitHub App client with fixed destinations and ephemeral credentials."""

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.github.schemas import (
    GitHubBranch,
    GitHubCommit,
    GitHubCommitComparison,
    GitHubHistoryBundle,
    GitHubInstallation,
    GitHubInstallationToken,
    GitHubOAuthToken,
    GitHubPullRequest,
    GitHubRepository,
    GitHubUser,
)

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_VERSION = "2026-03-10"
MAX_PAGES = 10
PAGE_SIZE = 100
MAX_INSTALLATION_TOKEN_REPOSITORIES = 500
MAX_HISTORY_LIMIT = 10
HTTP_NOT_FOUND = 404
HTTP_FORBIDDEN = 403
HTTP_TOO_MANY_REQUESTS = 429
_REPOSITORY_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class GitHubAPIError(RuntimeError):
    """Safe GitHub dependency failure without response bodies or credentials."""


class GitHubRepositoryNotFoundError(GitHubAPIError):
    """The public repository does not exist or is not visible."""


class GitHubRepositoryPrivateError(GitHubAPIError):
    """The requested repository is not publicly accessible."""


class GitHubRateLimitError(GitHubAPIError):
    """GitHub refused the request due to a provider rate limit."""


@dataclass(frozen=True, slots=True)
class PublicGitHubRepository:
    repository: GitHubRepository
    default_branch_sha: str


class GitHubClientProtocol(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> SecretStr: ...

    async def get_authenticated_user(self, access_token: SecretStr) -> GitHubUser: ...

    async def list_user_installations(
        self,
        access_token: SecretStr,
    ) -> Sequence[GitHubInstallation]: ...

    async def create_installation_token(self, installation_id: int) -> SecretStr: ...

    async def create_repository_installation_token(
        self, installation_id: int, *, repository_id: int
    ) -> SecretStr: ...

    async def compare_repository_commits(
        self,
        installation_token: SecretStr,
        *,
        owner: str,
        repository: str,
        base: str,
        head: str,
    ) -> GitHubCommitComparison: ...

    async def list_installation_repositories(
        self,
        installation_token: SecretStr,
    ) -> Sequence[GitHubRepository]: ...

    async def get_public_repository(
        self, *, owner: str, repository: str
    ) -> PublicGitHubRepository: ...

    async def compare_public_repository_commits(
        self, *, owner: str, repository: str, base: str, head: str
    ) -> GitHubCommitComparison: ...

    async def get_public_repository_history(
        self, *, owner: str, repository: str, revision: str, limit: int
    ) -> Sequence[GitHubHistoryBundle]: ...

    async def close(self) -> None: ...


class GitHubHistoryClientProtocol(Protocol):
    async def create_repository_installation_token(
        self, installation_id: int, *, repository_id: int
    ) -> SecretStr: ...

    async def get_repository_history(
        self,
        installation_token: SecretStr,
        *,
        owner: str,
        repository: str,
        revision: str,
        limit: int,
    ) -> Sequence[GitHubHistoryBundle]: ...

    async def get_public_repository_history(
        self, *, owner: str, repository: str, revision: str, limit: int
    ) -> Sequence[GitHubHistoryBundle]: ...


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
        return await self._create_installation_token(installation_id, repository_ids=None)

    async def create_repository_installation_token(
        self, installation_id: int, *, repository_id: int
    ) -> SecretStr:
        """Mint a token restricted to one server-authorized repository."""
        return await self._create_installation_token(
            installation_id, repository_ids=(repository_id,)
        )

    async def _create_installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: Sequence[int] | None,
    ) -> SecretStr:
        app_token = self._create_app_jwt()
        body: dict[str, object] = {
            "permissions": {
                "contents": "read",
                "metadata": "read",
                "pull_requests": "read",
            }
        }
        if repository_ids is not None:
            if (
                not repository_ids
                or len(repository_ids) > MAX_INSTALLATION_TOKEN_REPOSITORIES
                or any(item <= 0 for item in repository_ids)
            ):
                raise GitHubAPIError
            body["repository_ids"] = list(repository_ids)
        response = await self._request(
            "POST",
            f"{GITHUB_API_ROOT}/app/installations/{installation_id}/access_tokens",
            token=SecretStr(app_token),
            json=body,
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

    async def get_public_repository(self, *, owner: str, repository: str) -> PublicGitHubRepository:
        """Resolve trusted public identity and default-branch SHA through fixed API paths."""
        if (
            _REPOSITORY_SEGMENT.fullmatch(owner) is None
            or _REPOSITORY_SEGMENT.fullmatch(repository) is None
        ):
            raise GitHubRepositoryNotFoundError
        response = await self._public_request(f"{GITHUB_API_ROOT}/repos/{owner}/{repository}")
        try:
            metadata = GitHubRepository.model_validate(response.json())
        except (TypeError, ValueError, ValidationError) as error:
            raise GitHubAPIError from error
        if metadata.private:
            raise GitHubRepositoryPrivateError
        branch = quote(metadata.default_branch, safe="")
        branch_response = await self._public_request(
            f"{GITHUB_API_ROOT}/repos/{metadata.owner.login}/{metadata.name}/branches/{branch}"
        )
        try:
            branch_data = GitHubBranch.model_validate(branch_response.json())
        except (TypeError, ValueError, ValidationError) as error:
            raise GitHubAPIError from error
        if branch_data.name != metadata.default_branch:
            raise GitHubAPIError
        return PublicGitHubRepository(metadata, branch_data.commit.sha)

    async def compare_public_repository_commits(
        self, *, owner: str, repository: str, base: str, head: str
    ) -> GitHubCommitComparison:
        token = self._public_token()
        return await self._compare_repository_commits(
            token, owner=owner, repository=repository, base=base, head=head
        )

    async def get_public_repository_history(
        self, *, owner: str, repository: str, revision: str, limit: int
    ) -> Sequence[GitHubHistoryBundle]:
        return await self._get_repository_history(
            self._public_token(),
            owner=owner,
            repository=repository,
            revision=revision,
            limit=limit,
        )

    async def get_repository_history(
        self,
        installation_token: SecretStr,
        *,
        owner: str,
        repository: str,
        revision: str,
        limit: int,
    ) -> Sequence[GitHubHistoryBundle]:
        """Read bounded history through fixed repository-scoped GitHub API paths."""
        return await self._get_repository_history(
            installation_token,
            owner=owner,
            repository=repository,
            revision=revision,
            limit=limit,
        )

    async def _get_repository_history(
        self,
        token: SecretStr | None,
        *,
        owner: str,
        repository: str,
        revision: str,
        limit: int,
    ) -> Sequence[GitHubHistoryBundle]:
        if (
            _REPOSITORY_SEGMENT.fullmatch(owner) is None
            or _REPOSITORY_SEGMENT.fullmatch(repository) is None
            or re.fullmatch(r"[0-9a-f]{7,40}", revision) is None
            or not 1 <= limit <= MAX_HISTORY_LIMIT
        ):
            raise GitHubAPIError
        base = f"{GITHUB_API_ROOT}/repos/{owner}/{repository}"
        response = await self._request(
            "GET",
            f"{base}/commits",
            token=token,
            params={"sha": revision, "per_page": limit, "page": 1},
            retry_attempts=2,
        )
        try:
            summaries = [GitHubCommit.model_validate(item) for item in response.json()]
        except (TypeError, ValueError, ValidationError) as error:
            raise GitHubAPIError from error
        bundles: list[GitHubHistoryBundle] = []
        for summary in summaries[:limit]:
            detail_response = await self._request(
                "GET",
                f"{base}/commits/{summary.sha}",
                token=token,
                retry_attempts=2,
            )
            pulls_response = await self._request(
                "GET",
                f"{base}/commits/{summary.sha}/pulls",
                token=token,
                params={"per_page": 3, "page": 1},
                retry_attempts=2,
            )
            try:
                commit = GitHubCommit.model_validate(detail_response.json())
                pull_requests = [
                    GitHubPullRequest.model_validate(item) for item in pulls_response.json()[:3]
                ]
                self._validate_history_identity(
                    owner=owner,
                    repository=repository,
                    expected_sha=summary.sha,
                    commit=commit,
                    pull_requests=pull_requests,
                )
                bundles.append(GitHubHistoryBundle(commit=commit, pull_requests=pull_requests))
            except (TypeError, ValueError, ValidationError) as error:
                raise GitHubAPIError from error
        return tuple(bundles)

    async def compare_repository_commits(
        self,
        installation_token: SecretStr,
        *,
        owner: str,
        repository: str,
        base: str,
        head: str,
    ) -> GitHubCommitComparison:
        """Compare server-selected SHAs through one fixed repository API path."""
        return await self._compare_repository_commits(
            installation_token,
            owner=owner,
            repository=repository,
            base=base,
            head=head,
        )

    async def _compare_repository_commits(
        self,
        token: SecretStr | None,
        *,
        owner: str,
        repository: str,
        base: str,
        head: str,
    ) -> GitHubCommitComparison:
        if (
            _REPOSITORY_SEGMENT.fullmatch(owner) is None
            or _REPOSITORY_SEGMENT.fullmatch(repository) is None
            or re.fullmatch(r"[0-9a-f]{40}", base) is None
            or re.fullmatch(r"[0-9a-f]{40}", head) is None
        ):
            raise GitHubAPIError
        response = await self._request(
            "GET",
            f"{GITHUB_API_ROOT}/repos/{owner}/{repository}/compare/{base}...{head}",
            token=token,
            params={"per_page": 100, "page": 1},
            retry_attempts=2,
        )
        try:
            return GitHubCommitComparison.model_validate(response.json())
        except (TypeError, ValueError, ValidationError) as error:
            raise GitHubAPIError from error

    def _public_token(self) -> SecretStr | None:
        value = self._settings.github_public_api_token.get_secret_value()
        return SecretStr(value) if value else None

    async def _public_request(self, url: str) -> httpx.Response:
        """Map public metadata failures and follow at most one verified GitHub identity redirect."""
        token = self._public_token()
        response = await self._request("GET", url, token=token, allow_error_status=True)
        if response.status_code in {301, 307, 308}:
            location = response.headers.get("location", "")
            parsed = urlsplit(location)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "api.github.com"
                or parsed.query
                or parsed.fragment
                or re.fullmatch(r"/repositories/[1-9][0-9]*", parsed.path) is None
            ):
                raise GitHubAPIError
            response = await self._request("GET", location, token=token, allow_error_status=True)
        if response.status_code == HTTP_NOT_FOUND:
            raise GitHubRepositoryNotFoundError
        if response.status_code in {HTTP_FORBIDDEN, HTTP_TOO_MANY_REQUESTS}:
            raise GitHubRateLimitError
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise GitHubAPIError from error
        return response

    @staticmethod
    def _validate_history_identity(
        *,
        owner: str,
        repository: str,
        expected_sha: str,
        commit: GitHubCommit,
        pull_requests: Sequence[GitHubPullRequest],
    ) -> None:
        expected_commit_url = f"https://github.com/{owner}/{repository}/commit/{commit.sha}"
        if commit.sha != expected_sha or commit.html_url.rstrip("/") != expected_commit_url:
            raise ValueError("github_commit_identity_mismatch")
        if any(
            pull.html_url.rstrip("/")
            != f"https://github.com/{owner}/{repository}/pull/{pull.number}"
            for pull in pull_requests
        ):
            raise ValueError("github_pull_request_identity_mismatch")

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
        retry_attempts: int = 1,
        allow_error_status: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "RepoLume/0.7",
        }
        if token is not None:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        for attempt in range(1, retry_attempts + 1):
            try:
                response = await self._http.request(method, url, headers=headers, **kwargs)
                if not allow_error_status:
                    response.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as error:
                if attempt == retry_attempts:
                    raise GitHubAPIError from error
            except httpx.HTTPStatusError as error:
                retryable = error.response.status_code in {429, 500, 502, 503, 504}
                if not retryable or attempt == retry_attempts:
                    raise GitHubAPIError from error
            else:
                return response
            await asyncio.sleep(0.1 * attempt)
        raise AssertionError("github_retry_exhausted")

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()
