"""Authenticated public GitHub repository import and manual refresh endpoints."""

import uuid
from typing import cast

from fastapi import APIRouter, Request, status

from app.api.repositories import _repository_response, _status_response
from app.auth.dependencies import CurrentUser
from app.core.config import Settings
from app.core.errors import APIError
from app.db.session import Database
from app.github.client import (
    GitHubAPIError,
    GitHubClientProtocol,
    GitHubRateLimitError,
    GitHubRepositoryNotFoundError,
    GitHubRepositoryPrivateError,
)
from app.github.public_urls import PublicRepositoryURLError, parse_public_repository_url
from app.queue import JobQueueProtocol, QueueUnavailableError
from app.schemas.errors import ErrorCode
from app.schemas.repositories import PublicRepositoryImportRequest, PublicRepositoryImportResponse
from app.services.public_repositories import (
    PublicRepositoryAccessError,
    PublicRepositoryLimitError,
    PublicRepositoryService,
    PublicRepositoryTooLargeError,
)

router = APIRouter()


def _service(request: Request) -> PublicRepositoryService:
    return PublicRepositoryService(
        database=cast(Database, request.app.state.database),
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        queue=cast(JobQueueProtocol, request.app.state.job_queue),
        settings=cast(Settings, request.app.state.settings),
    )


def _map_error(error: Exception) -> APIError:
    if isinstance(error, PublicRepositoryURLError):
        return APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_REPOSITORY_URL,
            message="Enter a valid public GitHub repository URL",
        )
    if isinstance(error, GitHubRepositoryPrivateError):
        return APIError(
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.REPOSITORY_PRIVATE,
            message="Private repositories require the RepoLume GitHub App",
        )
    if isinstance(error, GitHubRepositoryNotFoundError | PublicRepositoryAccessError):
        return APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Repository was not found",
        )
    if isinstance(error, GitHubRateLimitError | PublicRepositoryLimitError):
        return APIError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
            message="The public repository import limit was reached",
        )
    if isinstance(error, PublicRepositoryTooLargeError):
        return APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.REPOSITORY_TOO_LARGE,
            message="Repository exceeds the configured size limit",
        )
    return APIError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code=ErrorCode.SERVICE_UNAVAILABLE,
        message="Public GitHub repository access is temporarily unavailable",
    )


@router.post("/import")
async def import_public_repository(
    payload: PublicRepositoryImportRequest, request: Request, user: CurrentUser
) -> PublicRepositoryImportResponse:
    try:
        parsed = parse_public_repository_url(payload.repository_url)
        result = await _service(request).import_repository(user_id=user.id, parsed_url=parsed)
    except (
        PublicRepositoryURLError,
        GitHubAPIError,
        PublicRepositoryLimitError,
        PublicRepositoryTooLargeError,
        QueueUnavailableError,
    ) as error:
        raise _map_error(error) from error
    return PublicRepositoryImportResponse(
        repository=_repository_response(result.repository),
        job=None if result.job is None else _status_response(result.repository, result.job),
        already_current=result.already_current,
        reused_index=result.reused_index,
    )


@router.post("/{repository_id}/refresh")
async def refresh_public_repository(
    repository_id: uuid.UUID, request: Request, user: CurrentUser
) -> PublicRepositoryImportResponse:
    try:
        result = await _service(request).refresh(user_id=user.id, repository_id=repository_id)
    except (
        PublicRepositoryAccessError,
        GitHubAPIError,
        PublicRepositoryLimitError,
        PublicRepositoryTooLargeError,
        QueueUnavailableError,
    ) as error:
        if isinstance(error, GitHubRepositoryPrivateError):
            await _service(request).revoke(repository_id)
        raise _map_error(error) from error
    return PublicRepositoryImportResponse(
        repository=_repository_response(result.repository),
        job=None if result.job is None else _status_response(result.repository, result.job),
        already_current=result.already_current,
        reused_index=result.reused_index,
    )
