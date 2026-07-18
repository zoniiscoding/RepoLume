"""Authenticated repository selection, detail, and durable job status endpoints."""

import uuid
from typing import cast

from fastapi import APIRouter, Request, status

from app.auth.dependencies import CurrentUser
from app.core.config import Settings
from app.core.errors import APIError
from app.db.models.enums import RepositoryIndexingStatus
from app.db.models.indexing_job import IndexingJob
from app.db.models.repository import Repository
from app.db.session import Database
from app.github.client import GitHubAPIError, GitHubClientProtocol
from app.queue import JobQueueProtocol, QueueUnavailableError
from app.schemas.errors import ErrorCode
from app.schemas.repositories import (
    IndexingStatusResponse,
    RepositoryCreateRequest,
    RepositoryCreateResponse,
    RepositoryDetailResponse,
)
from app.services.installations import InstallationAccessError, InstallationService
from app.services.repositories import RepositoryAccessError, RepositoryJob, RepositoryService

router = APIRouter()


def _is_searchable(repository: Repository) -> bool:
    return (
        repository.index_version > 0
        and repository.last_indexed_commit_sha is not None
        and repository.indexing_status
        not in {
            RepositoryIndexingStatus.NOT_INDEXED,
            RepositoryIndexingStatus.ACCESS_REVOKED,
            RepositoryIndexingStatus.DELETING,
        }
    )


def _service(request: Request) -> RepositoryService:
    database = cast(Database, request.app.state.database)
    installations = InstallationService(
        database=database,
        github=cast(GitHubClientProtocol, request.app.state.github_client),
        settings=cast(Settings, request.app.state.settings),
    )
    return RepositoryService(
        database=database,
        queue=cast(JobQueueProtocol, request.app.state.job_queue),
        installations=installations,
        settings=cast(Settings, request.app.state.settings),
    )


def _repository_response(repository: Repository) -> RepositoryDetailResponse:
    return RepositoryDetailResponse(
        id=repository.id,
        installation_id=repository.installation_id,
        github_repository_id=repository.github_repository_id,
        github_owner=repository.github_owner,
        github_name=repository.github_name,
        github_full_name=repository.github_full_name,
        github_url=repository.github_url,
        is_private=repository.is_private,
        default_branch=repository.default_branch,
        primary_language=repository.primary_language,
        indexing_status=repository.indexing_status,
        indexing_progress=repository.indexing_progress,
        indexing_stage=repository.indexing_stage,
        size_bytes=repository.size_bytes,
        active_commit_sha=repository.last_indexed_commit_sha,
        active_index_version=repository.index_version,
        indexed_branch=repository.indexed_branch,
        latest_remote_commit_sha=repository.current_remote_sha,
        vector_count=repository.active_vector_count,
        searchable=_is_searchable(repository),
    )


def _status_response(repository: Repository, job: IndexingJob | None) -> IndexingStatusResponse:
    return IndexingStatusResponse(
        repository_id=repository.id,
        repository_status=repository.indexing_status,
        job_id=None if job is None else job.id,
        job_status=None if job is None else job.status,
        attempt=0 if job is None else job.attempt,
        progress=repository.indexing_progress if job is None else job.progress,
        stage=repository.indexing_stage if job is None else job.stage,
        error_code=None if job is None else job.error_code,
        safe_error_message=None if job is None else job.safe_error_message,
        discovered_file_count=0 if job is None else job.discovered_file_count,
        discovered_total_bytes=0 if job is None else job.discovered_total_bytes,
        skipped_file_counts={} if job is None else job.skipped_files_json,
        parsed_file_count=0 if job is None else job.parsed_file_count,
        partial_file_count=0 if job is None else job.partial_file_count,
        parser_skipped_file_count=0 if job is None else job.parser_skipped_file_count,
        symbol_count=0 if job is None else job.symbol_count,
        chunk_count=0 if job is None else job.chunk_count,
        parser_warning_counts={} if job is None else job.parser_warnings_json,
        call_site_count=0 if job is None else job.call_site_count,
        exact_edge_count=0 if job is None else job.exact_edge_count,
        ambiguous_edge_count=0 if job is None else job.ambiguous_edge_count,
        unresolved_call_count=0 if job is None else job.unresolved_call_count,
        graph_warning_count=0 if job is None else job.graph_warning_count,
        target_index_version=None if job is None else job.target_index_version,
        embedded_chunk_count=0 if job is None else job.embedded_chunk_count,
        vector_count=repository.active_vector_count if job is None else job.vector_count,
        active_vector_count=repository.active_vector_count,
        embedding_failed_count=0 if job is None else job.embedding_failed_count,
        embedding_skipped_count=0 if job is None else job.embedding_skipped_count,
        active_commit_sha=repository.last_indexed_commit_sha,
        active_index_version=repository.index_version,
        searchable=_is_searchable(repository),
        last_failure_category=(repository.indexing_error_code if job is None else job.error_code),
        heartbeat_at=None if job is None else job.heartbeat_at,
        completed_at=None if job is None else job.completed_at,
        requested_mode=None if job is None else job.requested_mode,
        actual_mode=None if job is None else job.actual_mode,
        full_rebuild_reason=None if job is None else job.full_rebuild_reason,
        changed_file_count=0 if job is None else job.changed_file_count,
        changed_file_counts={} if job is None else job.changed_files_json,
        reused_chunk_count=0 if job is None else job.reused_chunk_count,
        reembedded_chunk_count=0 if job is None else job.reembedded_chunk_count,
        graph_rebuilt=False if job is None else job.graph_rebuilt,
        indexed_branch=repository.indexed_branch,
        latest_remote_commit_sha=repository.current_remote_sha,
        last_delivery_status=repository.last_delivery_status,
        last_delivery_at=repository.last_delivery_at,
    )


@router.get("")
async def list_repositories(request: Request, user: CurrentUser) -> list[RepositoryDetailResponse]:
    repositories = await _service(request).list_authorized(user.id)
    return [_repository_response(item) for item in repositories]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_repository(
    payload: RepositoryCreateRequest,
    request: Request,
    user: CurrentUser,
) -> RepositoryCreateResponse:
    try:
        selected = await _service(request).select_repository(
            user_id=user.id,
            installation_id=payload.installation_id,
            github_repository_id=payload.github_repository_id,
        )
    except (InstallationAccessError, RepositoryAccessError) as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Repository was not found",
        ) from error
    except GitHubAPIError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="GitHub repository access is temporarily unavailable",
        ) from error
    except QueueUnavailableError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Indexing queue is temporarily unavailable",
        ) from error
    return RepositoryCreateResponse(
        repository=_repository_response(selected.repository),
        job=_status_response(selected.repository, selected.job),
    )


@router.get("/{repository_id}")
async def get_repository(
    repository_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
) -> RepositoryDetailResponse:
    try:
        repository = await _service(request).get_authorized(
            user_id=user.id,
            repository_id=repository_id,
        )
    except RepositoryAccessError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Repository was not found",
        ) from error
    return _repository_response(repository)


@router.get("/{repository_id}/status")
async def get_repository_status(
    repository_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
) -> IndexingStatusResponse:
    try:
        result = await _service(request).get_status(
            user_id=user.id,
            repository_id=repository_id,
        )
    except RepositoryAccessError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Repository was not found",
        ) from error
    if isinstance(result, RepositoryJob):
        return _status_response(result.repository, result.job)
    repository, job = result
    return _status_response(repository, job)


@router.post("/{repository_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_repository(
    repository_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
) -> RepositoryCreateResponse:
    try:
        refreshed = await _service(request).reindex(
            user_id=user.id,
            repository_id=repository_id,
        )
    except RepositoryAccessError as error:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Repository was not found",
        ) from error
    except QueueUnavailableError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Indexing queue is temporarily unavailable",
        ) from error
    return RepositoryCreateResponse(
        repository=_repository_response(refreshed.repository),
        job=_status_response(refreshed.repository, refreshed.job),
    )
