"""Strictly validated subsets of untrusted GitHub API and webhook data."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)
    email: str | None = Field(default=None, max_length=320)


class GitHubAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)
    type: str = Field(pattern=r"^(User|Organization)$")


class GitHubInstallation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    account: GitHubAccount
    permissions: dict[str, str] = Field(default_factory=dict)
    repository_selection: str = Field(pattern=r"^(all|selected)$")
    suspended_at: datetime | None = None


class GitHubRepositoryOwner(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(min_length=1, max_length=255)


class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    owner: GitHubRepositoryOwner
    name: str = Field(min_length=1, max_length=255)
    full_name: str = Field(min_length=1, max_length=512)
    html_url: str = Field(pattern=r"^https://github\.com/")
    private: bool
    default_branch: str = Field(min_length=1, max_length=255)
    language: str | None = Field(default=None, max_length=64)


class GitHubOAuthToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: Literal["bearer"] = "bearer"


class GitHubInstallationToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: SecretStr
    expires_at: datetime
