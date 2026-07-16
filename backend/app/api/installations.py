"""Authenticated installation and repository listing endpoints."""

import uuid
from typing import cast

from fastapi import APIRouter, Request, status

from app.auth.dependencies import CurrentUser
from app.core.config import Settings
from app.core.errors import APIError
from app.db.session import Database
from app.github.client import GitHubAPIError, GitHubClientProtocol
from app.schemas.errors import ErrorCode
from app.schemas.installations import InstallationResponse, RepositoryResponse
from app.services.installations import InstallationAccessError, InstallationService

router = APIRouter()


def _service(request: Request) -> InstallationService:
    return InstallationService(
        database=cast(Database, request.app.state.database),
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        settings=cast(Settings, request.app.state.settings),
    )


@router.get("")
async def list_installations(request: Request, user: CurrentUser) -> list[InstallationResponse]:
    installations = await _service(request).list_authorized_installations(user.id)
    return [
        InstallationResponse(
            id=item.id,
            github_installation_id=item.github_installation_id,
            account_type=item.account_type.value,
            account_login=item.account_login,
            status=item.status,
            repository_selection=item.repository_selection,
        )
        for item in installations
    ]


@router.get("/{installation_id}/repositories")
async def list_installation_repositories(
    installation_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
) -> list[RepositoryResponse]:
    try:
        repositories = await _service(request).synchronize_repositories(
            user_id=user.id,
            installation_id=installation_id,
        )
    except InstallationAccessError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Installation was not found",
        ) from error
    except GitHubAPIError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="GitHub repository access is temporarily unavailable",
        ) from error
    return [
        RepositoryResponse(
            id=item.id,
            github_repository_id=item.github_repository_id,
            github_owner=item.github_owner,
            github_name=item.github_name,
            github_full_name=item.github_full_name,
            github_url=item.github_url,
            is_private=item.is_private,
            default_branch=item.default_branch,
            primary_language=item.primary_language,
        )
        for item in repositories
    ]
