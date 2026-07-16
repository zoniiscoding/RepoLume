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
    heartbeat_at: datetime | None
    completed_at: datetime | None


class RepositoryCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: RepositoryDetailResponse
    job: IndexingStatusResponse
