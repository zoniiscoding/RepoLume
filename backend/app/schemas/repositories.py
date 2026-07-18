"""Authenticated repository selection and indexing-status contracts."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import IndexingJobStatus, RepositoryIndexingStatus


class RepositoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: uuid.UUID
    github_repository_id: int = Field(gt=0)


class RepositoryDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    installation_id: uuid.UUID
    github_repository_id: int
    github_owner: str
    github_name: str
    github_full_name: str
    github_url: str
    is_private: bool
    default_branch: str
    primary_language: str | None
    indexing_status: RepositoryIndexingStatus
    indexing_progress: int
    indexing_stage: str | None
    size_bytes: int | None
    active_commit_sha: str | None
    active_index_version: int
    vector_count: int
    searchable: bool


class IndexingStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: uuid.UUID
    repository_status: RepositoryIndexingStatus
    job_id: uuid.UUID | None
    job_status: IndexingJobStatus | None
    attempt: int
    progress: int
    stage: str | None
    error_code: str | None
    safe_error_message: str | None
    discovered_file_count: int
    discovered_total_bytes: int
    skipped_file_counts: dict[str, int]
    parsed_file_count: int
    partial_file_count: int
    parser_skipped_file_count: int
    symbol_count: int
    chunk_count: int
    parser_warning_counts: dict[str, int]
    call_site_count: int
    exact_edge_count: int
    ambiguous_edge_count: int
    unresolved_call_count: int
    graph_warning_count: int
    target_index_version: int | None
    embedded_chunk_count: int
    vector_count: int
    active_vector_count: int
    embedding_failed_count: int
    embedding_skipped_count: int
    active_commit_sha: str | None
    active_index_version: int
    searchable: bool
    last_failure_category: str | None
    heartbeat_at: datetime | None
    completed_at: datetime | None


class RepositoryCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: RepositoryDetailResponse
    job: IndexingStatusResponse
