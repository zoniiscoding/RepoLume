"""Security regression tests for public GitHub URL normalization."""

import pytest

from app.api.public_repositories import _map_error
from app.github.client import (
    GitHubAPIError,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubRepositoryPrivateError,
)
from app.github.public_urls import PublicRepositoryURLError, parse_public_repository_url
from app.schemas.errors import ErrorCode
from app.services.public_repositories import (
    PublicRepositoryAccessError,
    PublicRepositoryLimitError,
    PublicRepositoryTooLargeError,
)


@pytest.mark.parametrize(
    ("value", "canonical"),
    [
        ("https://github.com/owner/repository", "https://github.com/owner/repository"),
        ("https://github.com/owner/repository/", "https://github.com/owner/repository"),
        ("https://github.com/owner/repository.git", "https://github.com/owner/repository"),
    ],
)
def test_accepts_only_canonical_repository_forms(value: str, canonical: str) -> None:
    assert parse_public_repository_url(value).canonical_url == canonical


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/owner/repository",
        "https://evil.example/owner/repository",
        "https://github.com:443/owner/repository",
        "https://user:pass@github.com/owner/repository",
        "https://github.com/owner/repository?tab=readme",
        "https://github.com/owner/repository#readme",
        "https://github.com/owner/repository/issues",
        "https://github.com/owner/repository/tree/main",
        "git@github.com:owner/repository.git",
        "file:///tmp/repository",
        "https://127.0.0.1/owner/repository",
        "https://github.com/owner/%2e%2e",
        "https://github.com/owner%2frepository/name",
        "https://github.com/owner\\repository/name",
        "https://github.com//repository",
        " https://github.com/owner/repository",
    ],
)
def test_rejects_ssrf_ambiguous_and_non_repository_urls(value: str) -> None:
    with pytest.raises(PublicRepositoryURLError):
        parse_public_repository_url(value)


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (PublicRepositoryURLError(), 422, ErrorCode.INVALID_REPOSITORY_URL),
        (GitHubRepositoryPrivateError(), 409, ErrorCode.REPOSITORY_PRIVATE),
        (GitHubRepositoryNotFoundError(), 404, ErrorCode.NOT_FOUND),
        (PublicRepositoryAccessError(), 404, ErrorCode.NOT_FOUND),
        (GitHubRateLimitError(), 429, ErrorCode.RATE_LIMIT_EXCEEDED),
        (PublicRepositoryLimitError(), 429, ErrorCode.RATE_LIMIT_EXCEEDED),
        (PublicRepositoryTooLargeError(), 422, ErrorCode.REPOSITORY_TOO_LARGE),
        (GitHubAPIError(), 503, ErrorCode.SERVICE_UNAVAILABLE),
    ],
)
def test_public_repository_failures_map_to_bounded_safe_api_errors(
    error: Exception, status_code: int, code: ErrorCode
) -> None:
    mapped = _map_error(error)

    assert mapped.status_code == status_code
    assert mapped.code is code
    assert mapped.details is None
    assert 1 <= len(mapped.message) <= 100
