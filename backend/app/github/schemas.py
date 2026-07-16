"""Strictly validated subsets of untrusted GitHub API and webhook data."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


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

    login: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    owner: GitHubRepositoryOwner
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    full_name: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}/[A-Za-z0-9][A-Za-z0-9._-]{0,254}$"
    )
    html_url: str = Field(pattern=r"^https://github\.com/")
    private: bool
    default_branch: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
    language: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_repository_identity(self) -> "GitHubRepository":
        """Bind every provider field to one fixed github.com repository identity."""
        expected_full_name = f"{self.owner.login}/{self.name}"
        expected_url = f"https://github.com/{expected_full_name}"
        invalid_branch_fragments = ("..", "//", "@{", "\\")
        if self.full_name != expected_full_name or self.html_url.rstrip("/") != expected_url:
            raise ValueError
        if any(item in self.default_branch for item in invalid_branch_fragments):
            raise ValueError
        if self.default_branch.endswith(("/", ".", ".lock")):
            raise ValueError
        return self


class GitHubOAuthToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: Literal["bearer"] = "bearer"


class GitHubInstallationToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: SecretStr
    expires_at: datetime
