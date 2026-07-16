"""Authorized GitHub installation and repository response contracts."""

import uuid

from pydantic import BaseModel, ConfigDict

from app.db.models.enums import InstallationStatus, RepositorySelection


class InstallationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    github_installation_id: int
    account_type: str
    account_login: str
    status: InstallationStatus
    repository_selection: RepositorySelection


class RepositoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    github_repository_id: int
    github_owner: str
    github_name: str
    github_full_name: str
    github_url: str
    is_private: bool
    default_branch: str
    primary_language: str | None
